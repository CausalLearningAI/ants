import cv2
import numpy as np
import pandas as pd
from collections import deque
from scipy.spatial.distance import cdist

import argparse
import os
import sys
sys.path.append('./src')
import warnings
warnings.filterwarnings("ignore")
import time
from utils import get_time, angle_between

def get_parser():
    parser = argparse.ArgumentParser(description="Track ants in a video.")
    parser.add_argument("--data_dir", type=str, default="data/v2/", help="Data directory.")
    parser.add_argument("--verbose", default=False, action="store_true", help="Enable verbose output.")
    parser.add_argument("--radius", type=int, default=70, help="Radius for zooming in on ants.")
    parser.add_argument("--window", type=int, default=5, help="Window size for velocity smoothing.")
    parser.add_argument("--min_speed", type=float, default=3.0, help="Minimum speed for tracking ants.")
    parser.add_argument("--max_step", type=int, default=500, help="Maximum step distance for tracking.")
    return parser

def get_background(video_path: str, quantile: float, n = 40) -> np.ndarray:
    """
    Given a video path, return the background frame using quantile method.

    Args:
        video_path (str): Path to the video file.
        quantile (float): Quantile value for background calculation.
        n (int): Number of frames to use for background calculation.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Number of frames: {frame_count}")
    frame_indices = np.linspace(0, frame_count+1, num=n, dtype=int)
    frame_indices = frame_indices[1:-1]  # Exclude the first and last frame
    background_frames = []
    for i in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret:
            break
        background_frames.append(frame)
    cap.release()
    background_frames = np.array(background_frames)
    background = np.quantile(background_frames, quantile, axis=0).astype(np.uint8)

    return background

def get_blue_yellow_positions(frame, data_dir="data/v2"):
    """
    Given a frame, return the positions of the blue and yellow dots.
    """
    # Step 1: Convert the frame to HSV (use BGR even if in RGB to rever)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Step 2: Create masks for blue and yellow regions
    if "v2" in data_dir:
        # Blue dot range (cyan-blue, slightly desaturated)
        BLUE_LOWER = np.array([100, 80, 80])    # Allow desaturated and darker blue
        BLUE_UPPER = np.array([125, 255, 255]) 

        # Yellow/orange dot range (muted yellowish-orange)
        YELLOW_LOWER = np.array([0, 80, 80])  # Increase S and V to exclude grey and black
        YELLOW_UPPER = np.array([30, 255, 255])  # Keep the upper range unchanged
    else:   
        raise ValueError(f"Missing color bounds for {data_dir} dataset.")

    blue_mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
    yellow_mask = cv2.inRange(hsv, YELLOW_LOWER, YELLOW_UPPER)

    # Step 3: Calculate centroids for blue and yellow regions
    def get_largest_centroid(mask, min_area=30):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours) == 0:
            return None
        largest_contour = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest_contour) < min_area:
            return None
        M = cv2.moments(largest_contour)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            return (cx, cy)
        return None

    blue_centroid = get_largest_centroid(blue_mask)
    yellow_centroid = get_largest_centroid(yellow_mask)

    return blue_centroid, yellow_centroid

def merge_close_centroids(centroids):
    """
    Merge centroids that are too close together.
    """
    centroids = np.array(centroids)
    distances = cdist(centroids, centroids)
    merged = []
    used = set()
    for i in range(len(centroids)):
        if i in used:
            continue
        close_points = [j for j in range(len(centroids)) if distances[i, j] < 30]
        merged_centroid = np.mean(centroids[close_points], axis=0)
        merged.append(merged_centroid)
        used.update(close_points)
    return merged

def zoom(frame, centroid, radius=70):
    """
    Zoom into the frame around the centroid (square 2*radius x 2*radius). 
    Black pixel padding is added if the zoomed area exceeds frame boundaries.
    """
    if centroid is None:
        zoomed_frame = np.zeros((2 * radius, 2 * radius, 3), dtype=np.uint8)
    else:
        x, y = int(centroid[0]), int(centroid[1])  # x is column, y is row
        h, w = frame.shape[:2]

        top = max(0, y - radius)
        top_pad = max(0, radius - y)
        bottom = min(h, y + radius)
        bottom_pad = max(0, y + radius - h)

        left = max(0, x - radius)
        left_pad = max(0, radius - x)
        right = min(w, x + radius)
        right_pad = max(0, x + radius - w)

        zoomed_frame = np.zeros((2 * radius, 2 * radius, 3), dtype=np.uint8)
        zoomed_frame[top_pad:(2*radius - bottom_pad), left_pad:(2*radius - right_pad)] = frame[top:bottom, left:right]

    return zoomed_frame

# class KalmanFilter2D:
#     def __init__(self, dt=1.0):
#         self.kf = cv2.KalmanFilter(4, 2)  # 4 state variables: x, y, dx, dy; 2 measurements: x, y
#         self.dt = dt

#         # State transition matrix (incorporates velocity over time dt)
#         self.kf.transitionMatrix = np.array([[1, 0, dt, 0],
#                                              [0, 1, 0, dt],
#                                              [0, 0, 1,  0],
#                                              [0, 0, 0,  1]], np.float32)

#         # Measurement matrix (we directly observe x, y)
#         self.kf.measurementMatrix = np.array([[1, 0, 0, 0],
#                                               [0, 1, 0, 0]], np.float32)

#         # Covariance matrices
#         self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * 0.03  # Model noise
#         self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 1.0  # Observation noise

#         self.initialized = False

#     def update(self, coord):
#         if coord is not None:
#             measured = np.array([[np.float32(coord[0])],
#                                  [np.float32(coord[1])]])
#             if not self.initialized:
#                 # Initialize state: position + zero velocity
#                 self.kf.statePre = np.array([[coord[0]], [coord[1]], [0], [0]], dtype=np.float32)
#                 self.kf.statePost = np.array([[coord[0]], [coord[1]], [0], [0]], dtype=np.float32)
#                 self.initialized = True
#             self.kf.correct(measured)
#         pred = self.kf.predict()
#         return (int(pred[0]), int(pred[1]))

class RollingVelocitySmoother:
    # Simple moving average smoother for velocity estimation.
    def __init__(self, window=5):
        self.prev_pos = None
        self.vel_buffer = deque(maxlen=window)

    def update(self, pos):
        if self.prev_pos is None:
            self.prev_pos = pos
            return (0.0, 0.0)
        
        dx = pos[0] - self.prev_pos[0]
        dy = pos[1] - self.prev_pos[1]
        self.vel_buffer.append((dx, dy))
        self.prev_pos = pos

        # Compute mean dx and dy
        mean_dx = np.mean([v[0] for v in self.vel_buffer])
        mean_dy = np.mean([v[1] for v in self.vel_buffer])

        return (mean_dx, mean_dy)

def main(args):
    # === Load video ===
    video_dir = os.path.join(args.data_dir, "video")
    video_files = [f for f in os.listdir(video_dir) if f.endswith('.mkv')]
    if not video_files:
        if args.verbose: print("No video files found in the specified directory.")
        return
    # create output directories
    os.makedirs(os.path.join(args.data_dir, "tracking", "preview"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "tracking", "position"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "tracking", "background", "blue"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "tracking", "background", "yellow"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "tracking", "background", "focal"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "tracking", "nobackground", "blue"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "tracking", "nobackground", "yellow"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "tracking", "nobackground", "focal"), exist_ok=True)
    if "v1" in args.data_dir:
        quantile = 0.99
    elif "v2" in args.data_dir:
        quantile = 0.85
    else:
        raise ValueError(f"Unknown data directory: {args.data_dir}. Please specify a valid dataset (v1 or v2).")
    for video_file in video_files:
        print(f"Processing video: {video_file}", flush=True)
        video_path = os.path.join(video_dir, video_file)
        background = get_background(video_path, quantile, n=20)
        cap = cv2.VideoCapture(video_path)

        # === Setup VideoWriter ===
        tracking_video_path = os.path.join(args.data_dir, "tracking", "preview", video_file[:-4]+".mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        tracking = cv2.VideoWriter(tracking_video_path, fourcc, fps, (width, height))

        width = 2 * args.radius
        height = 2 * args.radius
        # === Setup Zoom Blue Background VideoWriter ===
        zoom_blue_b_video_path = os.path.join(args.data_dir, "tracking", "background", "blue", video_file[:-4]+".mp4")
        zoom_blue_b = cv2.VideoWriter(zoom_blue_b_video_path, fourcc, fps, (width, height))

        # === Setup Zoom Yellow Background VideoWriter ===
        zoom_yellow_b_video_path = os.path.join(args.data_dir, "tracking", "background", "yellow", video_file[:-4]+".mp4")
        zoom_yellow_b = cv2.VideoWriter(zoom_yellow_b_video_path, fourcc, fps, (width, height))

        # === Setup Zoom Focal Background VideoWriter ===
        zoom_focal_b_video_path = os.path.join(args.data_dir, "tracking", "background", "focal", video_file[:-4]+".mp4")
        zoom_focal_b = cv2.VideoWriter(zoom_focal_b_video_path, fourcc, fps, (width, height))

        # === Setup Zoom Blue No Background VideoWriter ===
        zoom_blue_nb_video_path = os.path.join(args.data_dir, "tracking", "nobackground", "blue", video_file[:-4]+".mp4")
        zoom_blue_nb = cv2.VideoWriter(zoom_blue_nb_video_path, fourcc, fps, (width, height))

        # === Setup Zoom Yellow No Background VideoWriter ===
        zoom_yellow_nb_video_path = os.path.join(args.data_dir, "tracking", "nobackground", "yellow", video_file[:-4]+".mp4")
        zoom_yellow_nb = cv2.VideoWriter(zoom_yellow_nb_video_path, fourcc, fps, (width, height))

        # === Setup Zoom Focal No Background VideoWriter ===
        zoom_focal_nb_video_path = os.path.join(args.data_dir, "tracking", "nobackground", "focal", video_file[:-4]+".mp4")
        zoom_focal_nb = cv2.VideoWriter(zoom_focal_nb_video_path, fourcc, fps, (width, height))

        # Initialize tracking variables
        tracked_data = []
        frame_num = 0

        color_map = {
            "blue": (255, 0, 0),
            "yellow": (0, 255, 255),
            "green": (0, 255, 0),
            "red": (0, 0, 255),
            "white": (255, 255, 255),
            "orange": (0, 165, 255),
            "skyblue": (255, 255, 0),
            "black": (0, 0, 0),
        }

        # Inizialization
        # TODO: get the initial positions of the blue and yellow dots
        ants_centroids = [(0, 0), (500, 500), (1000, 1000)]
        ants_norms = [(0, 0), (0, 0), (0, 0)]
        yellow_index = 1
        blue_index = 2
        blue_nx, blue_ny = 0, 0
        yellow_nx, yellow_ny = 0, 0
        focal_nx, focal_ny = 0, 0
        blue_vx_main, blue_vy_main = 0, 0
        yellow_vx_main, yellow_vy_main = 0, 0
        focal_vx_main, focal_vy_main = 0, 0

        blue_v_smoother = RollingVelocitySmoother(window=args.window)
        yellow_v_smoother = RollingVelocitySmoother(window=args.window)
        focal_v_smoother = RollingVelocitySmoother(window=args.window)

        while True: # and frame_num < 1000:
            ret, frame = cap.read()
            if not ret:
                break

            # 0. Subtract the background from the current frame
            frame_noback = cv2.absdiff(frame, background)
            frame_tracking = frame.copy()

            # Inizialization
            if frame_num == 0:
                blue_centroid, yellow_centroid = get_blue_yellow_positions(frame, args.data_dir)

            # 1. Get blue and yellow positions
            blue_centroid_new, yellow_centroid_new = get_blue_yellow_positions(frame, args.data_dir)
            missing_blue = 0
            missing_yellow = 0
            if blue_centroid_new is None:
                missing_blue = 1
            if yellow_centroid_new is None:
                missing_yellow = 1
            if args.verbose: print(f"Frame {frame_num} (new): Blue: {blue_centroid_new}, Yellow: {yellow_centroid_new}", flush=True)
            if blue_centroid_new and cdist([blue_centroid_new], [blue_centroid])[0][0] < args.max_step:
                blue_centroid = blue_centroid_new
            else:
                blue_centroid = ants_centroids[blue_index]

            if yellow_centroid_new and cdist([yellow_centroid_new], [yellow_centroid])[0][0] < args.max_step:
                yellow_centroid = yellow_centroid_new
            else:
                yellow_centroid = ants_centroids[yellow_index]
            if args.verbose: print(f"Frame {frame_num}: Blue: {blue_centroid}, Yellow: {yellow_centroid}", flush=True)
            
            # 2. Get ants positions
            frame_noback_gray = cv2.cvtColor(frame_noback, cv2.COLOR_BGR2GRAY)
            _, threshold = cv2.threshold(frame_noback_gray, np.percentile(frame_noback_gray, 99.5), 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            filtered_contours = [cnt for cnt in contours if cv2.contourArea(cnt) > 100]
            filtered_contours = sorted(filtered_contours, key=cv2.contourArea, reverse=True)
            centroids = []
            norms = []
            for cnt in filtered_contours:
                M = cv2.moments(cnt)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    centroids.append((cx, cy))  
                    mean, eigenvectors, eigenvalues = cv2.PCACompute2(cnt.reshape(-1, 2).astype(np.float32), 
                                                                      mean=np.array([]))
                    norms.append(eigenvectors[0])                            
            ants_centroids_new = merge_close_centroids(centroids)
            if args.verbose: print(f"Frame {frame_num}: Ants centroids (new): {ants_centroids_new}", flush=True)
            if len(ants_centroids_new)<1:
                print("Not ants detect.", flush=True)
            elif len(ants_centroids_new)==1:
                pass
            elif len(ants_centroids_new)==2:
                isolated_centroid = ants_centroids_new[1]
                isolated_distances = cdist([isolated_centroid], ants_centroids)
                isolated_index = np.argmin(isolated_distances)
                ants_centroids[isolated_index] = isolated_centroid
            else:
                ants_centroids = ants_centroids_new[:3]
                ants_norms = norms[:3]

            # 3. Identify ants
            if args.verbose: print(f"Frame {frame_num}: Ants centroids: {ants_centroids}", flush=True)
            blue_distances = cdist([blue_centroid], ants_centroids)
            if args.verbose: print(f"Frame {frame_num}: Blue distances: {blue_distances}", flush=True)
            blue_index = np.argmin(blue_distances)
            yellow_distances = cdist([yellow_centroid], ants_centroids)
            if args.verbose: print(f"Frame {frame_num}: Yellow distances: {yellow_distances}", flush=True)
            yellow_index = np.argmin(yellow_distances)
            if blue_index == yellow_index:
                if blue_distances[0][blue_index] < yellow_distances[0][yellow_index]:
                    yellow_index = np.argsort(yellow_distances[0])[1]
                else:
                    blue_index = np.argsort(blue_distances[0])[1]
            focal_index = 3-(blue_index+yellow_index)
            dist_B2F = cdist([ants_centroids[blue_index]], [ants_centroids[focal_index]])[0][0]
            dist_Y2F = cdist([ants_centroids[yellow_index]], [ants_centroids[focal_index]])[0][0]
            dist_B2Y = cdist([ants_centroids[blue_index]], [ants_centroids[yellow_index]])[0][0]
            
            # Save tracking data
            blue_x, blue_y = ants_centroids[blue_index]
            yellow_x, yellow_y = ants_centroids[yellow_index]
            focal_x, focal_y = ants_centroids[focal_index]

            blue_vx, blue_vy = blue_v_smoother.update((blue_x, blue_y))
            yellow_vx, yellow_vy = yellow_v_smoother.update((yellow_x, yellow_y))
            focal_vx, focal_vy = focal_v_smoother.update((focal_x, focal_y))
            
            if np.linalg.norm([blue_vx, blue_vy]) > args.min_speed:
                blue_vx_main, blue_vy_main = blue_vx, blue_vy
            if np.linalg.norm([yellow_vx, yellow_vy]) > args.min_speed:
                yellow_vx_main, yellow_vy_main = yellow_vx, yellow_vy
            if np.linalg.norm([focal_vx, focal_vy]) > args.min_speed:
                focal_vx_main, focal_vy_main = focal_vx, focal_vy

            blue_nx, blue_ny = ants_norms[blue_index]
            if np.dot([blue_nx, blue_ny], [blue_vx_main, blue_vy_main]) < 0:
                blue_nx, blue_ny = -blue_nx, -blue_ny
            yellow_nx, yellow_ny = ants_norms[yellow_index]
            if np.dot([yellow_nx, yellow_ny], [yellow_vx_main, yellow_vy_main]) < 0:
                yellow_nx, yellow_ny = -yellow_nx, -yellow_ny
            focal_nx, focal_ny = ants_norms[focal_index]
            if np.dot([focal_nx, focal_ny], [focal_vx_main, focal_vy_main]) < 0:
                focal_nx, focal_ny = -focal_nx, -focal_ny

            angle_B2F = angle_between(blue_nx, blue_ny, focal_nx, focal_ny)
            angle_Y2F = angle_between(yellow_nx, yellow_ny, focal_nx, focal_ny)
            angle_B2Y = angle_between(blue_nx, blue_ny, yellow_nx, yellow_ny)
            
            tracked_data.append({
                "frame": frame_num,
                "blue_x": blue_x, "blue_y": blue_y,
                "yellow_x": yellow_x, "yellow_y": yellow_y,
                "focal_x": focal_x, "focal_y": focal_y,
                "blue_vx": blue_vx, "blue_vy": blue_vy,
                "yellow_vx": yellow_vx, "yellow_vy": yellow_vy,
                "focal_vx": focal_vx, "focal_vy": focal_vy,
                "dist_B2F": dist_B2F, "dist_Y2F": dist_Y2F, "dist_B2Y": dist_B2Y,
                "angle_B2F": angle_B2F, "angle_Y2F": angle_Y2F, "angle_B2Y": angle_B2Y,
                #"missing_blue": missing_blue, "missing_yellow": missing_yellow,
            })
            
            # Color marking
            cv2.circle(frame_tracking, (int(blue_centroid[0]), int(blue_centroid[1])), 5, color_map["skyblue"], -1)
            cv2.circle(frame_tracking, (int(yellow_centroid[0]), int(yellow_centroid[1])), 5, color_map["orange"], -1)
            for color, index in zip(["blue", "yellow", "green"], [blue_index, yellow_index, focal_index]):
                cv2.circle(frame_tracking, (int(ants_centroids[index][0]), int(ants_centroids[index][1])), 5, color_map[color], -1)
            cv2.arrowedLine(frame_tracking, 
                            (int(blue_x), int(blue_y)), 
                            (int(blue_x + 10 * (blue_vx)), int(blue_y + 10 * (blue_vy))), 
                            color_map["blue"], 2)
            cv2.arrowedLine(frame_tracking, 
                            (int(yellow_x), int(yellow_y)), 
                            (int(yellow_x + 10 * (yellow_vx)), int(yellow_y + 10 * (yellow_vy))), 
                            color_map["yellow"], 2)
            cv2.arrowedLine(frame_tracking, 
                            (int(focal_x), int(focal_y)), 
                            (int(focal_x + 10 * (focal_vx)), int(focal_y + 10 * (focal_vy))), 
                            color_map["green"], 2)
            cv2.arrowedLine(frame_tracking,
                            (int(blue_x), int(blue_y)), 
                            (int(blue_x + 30 * (blue_nx)), int(blue_y + 30 * (blue_ny))), 
                            color_map["white"], 2)
            cv2.arrowedLine(frame_tracking,
                            (int(yellow_x), int(yellow_y)), 
                            (int(yellow_x + 30 * (yellow_nx)), int(yellow_y + 30 * (yellow_ny))), 
                            color_map["white"], 2)
            cv2.arrowedLine(frame_tracking,
                            (int(focal_x), int(focal_y)), 
                            (int(focal_x + 30 * (focal_nx)), int(focal_y + 30 * (focal_ny))), 
                            color_map["white"], 2)
            
            # Write the frame to the output video
            tracking.write(frame_tracking)
            zoom_blue_b.write(zoom(frame, [blue_x, blue_y], args.radius))
            zoom_yellow_b.write(zoom(frame, [yellow_x, yellow_y], args.radius))
            zoom_focal_b.write(zoom(frame, [focal_x, focal_y], args.radius))
            zoom_blue_nb.write(zoom(frame_noback, [blue_x, blue_y], args.radius))
            zoom_yellow_nb.write(zoom(frame_noback, [yellow_x, yellow_y], args.radius))
            zoom_focal_nb.write(zoom(frame_noback, [focal_x, focal_y], args.radius))

            frame_num += 1

        # Release resources
        cap.release()
        tracking.release()
        zoom_blue_b.release()
        zoom_yellow_b.release()
        zoom_focal_b.release()
        zoom_blue_nb.release()
        zoom_yellow_nb.release()
        zoom_focal_nb.release()

        # Save tracking data to CSV
        df = pd.DataFrame(tracked_data)
        df.to_csv(os.path.join(args.data_dir, "tracking", "position", f"{video_file[:-4]}.csv"), index=False)
        print("Tracking saved to tracking.csv", flush=True)

if __name__ == "__main__":
    args = get_parser().parse_args()
    start_time = time.time()
    main(args)
    get_time(start_time)

