"""
Analyser module
"""
import threading
import cv2
import time
import numpy as np
import numpy.matlib

# Gaussian smoothing
kernel_size = 3

# Canny Edge Detector
low_threshold = 100
high_threshold = 110

# Region-of-interest vertices
# We want a trapezoid shape, with bottom edge at the bottom of the image
trap_bottom_width = 1.2  # width of bottom edge of trapezoid, expressed as percentage of image width
trap_top_width = 0.38  # ditto for top edge of trapezoid
trap_height = 0.97  # height of the trapezoid expressed as percentage of image height

# Hough Transform
rho = 2 # distance resolution in pixels of the Hough grid
theta = 1 * np.pi/180 # angular resolution in radians of the Hough grid
threshold = 15	 # minimum number of votes (intersections in Hough grid cell)
min_line_length = 10 #minimum number of pixels making up a line
max_line_gap = 20	# maximum gap in pixels between connectable line segments

alpha = 0.8
beta = 1.
gamma = 0.

right_x1_coord = 1
left_x1_coord = 1
y1_coord = 1

class Analyser(object):
    """
    Analyser class
    - responsible to analyse the current frame
    - detect lanes, cars, obstacles, road signs, etc
    and send the commands to SerialManager
    """
    def __init__(self):
        self.__current_frame = None
        self.__encode_parameter = [int(cv2.IMWRITE_JPEG_QUALITY), 60]
        self.__command_timer = 0

    def analyse(self, frame_queue, autonomous_states_queue, commands_queue, analysed_frame_queue):
        """
        get the current frame from FRAME_QUEUE of CarManager and analyse
        """
        current_thread = threading.currentThread()
        self.__command_timer = time.time()
        while getattr(current_thread, 'is_running', True):
            string_data = frame_queue.get(True, None)
            frame = numpy.fromstring(string_data, dtype='uint8')
            self.__current_frame = cv2.imdecode(frame, 1)
            frame_queue.task_done()

            if getattr(current_thread, 'is_analysing', True):
                self.__current_frame = self.__lane_assist(autonomous_states_queue, commands_queue)
                result, encrypted_image = \
                    cv2.imencode('.jpg', self.__current_frame, self.__encode_parameter)
                if bool(result) is False:
                    break
                analysed_frame = numpy.array(encrypted_image)
                analysed_frame_queue.put(analysed_frame, True, None)
            else:
                result, encrypted_image = \
                    cv2.imencode('.jpg', self.__current_frame, self.__encode_parameter)
                if bool(result) is False:
                    break
                analysed_frame = numpy.array(encrypted_image)
                analysed_frame_queue.put(analysed_frame.tostring(), True, None)

            #autonomous_states_queue.put()
            #commands_queue.put()

    def __gaussian_blur(self, img, kernel_size):
        """Applies a Gaussian Noise kernel"""
        return cv2.GaussianBlur(img, (kernel_size, kernel_size), 0)

    def __grayscale(self, img):
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    def __canny(self, img, low_threshold, high_threshold):
        """Applies the Canny transform"""
        return cv2.Canny(img, low_threshold, high_threshold)

    def __region_of_interest(self, img, vertices):

        mask = np.zeros_like(img)

        #defining a 3 channel or 1 channel color to fill the mask with depending on the input image
        if len(img.shape) > 2:
            channel_count = img.shape[2]  # i.e. 3 or 4 depending on your image
            ignore_mask_color = (255,) * channel_count
        else:
            ignore_mask_color = 255

        #filling pixels inside the polygon defined by "vertices" with the fill color
        cv2.fillPoly(mask, vertices, ignore_mask_color)

        #returning the image only where mask pixels are nonzero
        masked_image = cv2.bitwise_and(img, mask)
        return masked_image

    def __draw_lines(self, img, lines, color=[255, 0, 0], thickness=5):
        if lines is None:
            return
        if len(lines) == 0:
            return
        draw_right = True
        draw_left = True

        # Find slopes of all lines
        # But only care about lines where abs(slope) > slope_threshold
        slope_threshold = 0.5
        slopes = []
        new_lines = []
        for line in lines:
            x1, y1, x2, y2 = line[0]  # line = [[x1, y1, x2, y2]]

            # Calculate slope
            if x2 - x1 == 0.:  # corner case, avoiding division by 0
                slope = 999.  # practically infinite slope
            else:
                slope = (y2 - y1) / (x2 - x1)

            # Filter lines based on slope
            if abs(slope) > slope_threshold:
                slopes.append(slope)
                new_lines.append(line)

        lines = new_lines

        # Split lines into right_lines and left_lines, representing the right and left lane lines
        # Right/left lane lines must have positive/negative slope,
        # and be on the right/left half of the image
        right_lines = []
        left_lines = []
        for i, line in enumerate(lines):
            x1, y1, x2, y2 = line[0]
            img_x_center = img.shape[1] / 2  # x coordinate of center of image
            if slopes[i] > 0 and x1 > img_x_center and x2 > img_x_center:
                right_lines.append(line)
            elif slopes[i] < 0 and x1 < img_x_center and x2 < img_x_center:
                left_lines.append(line)

        # Run linear regression to find best fit line for right and left lane lines
        # Right lane lines
        right_lines_x = []
        right_lines_y = []

        for line in right_lines:
            x1, y1, x2, y2 = line[0]

            right_lines_x.append(x1)
            right_lines_x.append(x2)

            right_lines_y.append(y1)
            right_lines_y.append(y2)

        if len(right_lines_x) > 0:
            right_m, right_b = np.polyfit(right_lines_x, right_lines_y, 1)  # y = m*x + b
        else:
            right_m, right_b = 1, 1
            draw_right = False

        # Left lane lines
        left_lines_x = []
        left_lines_y = []

        for line in left_lines:
            x1, y1, x2, y2 = line[0]

            left_lines_x.append(x1)
            left_lines_x.append(x2)

            left_lines_y.append(y1)
            left_lines_y.append(y2)

        if len(left_lines_x) > 0:
            left_m, left_b = np.polyfit(left_lines_x, left_lines_y, 1)  # y = m*x + b
        else:
            left_m, left_b = 1, 1
            draw_left = False

        # Find 2 end points for right and left lines, used for drawing the line
        # y = m*x + b --> x = (y - b)/m
        y1 = img.shape[0]
        y2 = img.shape[0] * (1 - trap_height)

        right_x1 = (y1 - right_b) / right_m
        right_x2 = (y2 - right_b) / right_m

        left_x1 = (y1 - left_b) / left_m
        left_x2 = (y2 - left_b) / left_m

        # Convert calculated end points from float to int
        y1 = int(y1)
        y2 = int(y2)
        right_x1 = int(right_x1)
        right_x2 = int(right_x2)
        left_x1 = int(left_x1)
        left_x2 = int(left_x2)

        # Draw the right and left lines on image
        if draw_right:
            cv2.line(img, (right_x1, y1), (right_x2, y2), color, thickness)

        if draw_left:
            cv2.line(img, (left_x1, y1), (left_x2, y2), color, thickness)

        global right_x1_coord
        global y1_coord
        global left_x1_coord

        right_x1_coord = right_x1
        y1_coord = y1
        left_x1_coord = left_x1

        '''
        cv2.circle(img, (right_x1, y1), 100, color, thickness)
        cv2.circle(img, (left_x1, y1), 100, color, thickness)
        '''

    def __hough_lines(self, img, rho, theta, threshold, min_line_len, max_line_gap):
        lines = cv2.HoughLinesP(img, rho, theta, threshold, np.array([]), \
            minLineLength=min_line_len, maxLineGap=max_line_gap)

        (x1, x2) = img.shape
        dt = np.dtype(np.uint8)
        line_img = np.zeros((x1, x2, 3), dt)
        self.__draw_lines(line_img, lines)

        return line_img

    def __lane_assist(self, autonomous_states_queue, commands_queue):
        grey = self.__grayscale(self.__current_frame)
        blur_grey = self.__gaussian_blur(grey, kernel_size)

        edges = self.__canny(blur_grey, low_threshold, high_threshold)

        imshape = self.__current_frame.shape
        vertices = np.array([[\
			((imshape[1] * (1 - trap_bottom_width)) // 2, imshape[0]),\
			((imshape[1] * (1 - trap_top_width)) // 2, imshape[0] - imshape[0] * trap_height),\
			(imshape[1] - (imshape[1] * (1 - trap_top_width)) // 2, imshape[0] - imshape[0] * trap_height),\
			(imshape[1] - (imshape[1] * (1 - trap_bottom_width)) // 2, imshape[0])]]\
			, dtype=np.int32)

        masked_image = self.__region_of_interest(edges, vertices)

        line_image = self.__hough_lines(masked_image, rho, theta, \
            threshold, min_line_length, max_line_gap)

        final_image = self.__current_frame.astype('uint8')

        cv2.addWeighted(self.__current_frame, alpha, line_image, beta, gamma, final_image)

        final_image2 = final_image.astype('uint8')

        final_x = (right_x1_coord + left_x1_coord)/2

        cv2.circle(final_image2, (final_x, y1_coord), 50, [255, 0, 0], 5)

        height, width, channels = self.__current_frame.shape

        if time.time() - self.__command_timer > 300.0 / 1000.0:
            print width, final_x
            if (final_x < width/2 - 20) or (final_x > width/2 + 20):
                if final_x < width/2:
                    print 'left'
                    commands_queue.put('4/', True, None)
                else:
                    print 'right'
                    commands_queue.put('5/', True, None)
            else:
                commands_queue.put('1/1/', True, None)
            self.__command_timer = time.time()

        return final_image2
