import os
import argparse
import yaml
import numpy as np
import pandas
import cv2
import json
from scipy.spatial.transform import Rotation as R
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D


# def plot_cameras(extrinsic_matrices):
#     """
#     Visualizes the extrinsics of multiple cameras in 3D space.

#     Parameters:
#     - extrinsic_matrices (numpy.ndarray): Array of camera extrinsics matrices (Nx4x4).
#     https://stackoverflow.com/questions/8178467/how-to-plot-the-camera-and-image-positions-from-camera-calibration-data
#     """
#     ax = plt.figure().add_subplot(projection="3d")

#     for camera_extrinsics in extrinsic_matrices:
#         # Extract translation and rotation from camera extrinsics matrix
#         translation = camera_extrinsics[:3, 3]
#         rotation_matrix = camera_extrinsics[:3, :3]

#         # Plot camera position
#         ax.scatter(*translation, marker="o")

#         # Plot camera orientation axes
#         origin = translation
#         for i in range(3):
#             axis_direction = rotation_matrix[:, i]
#             if i == 0:
#                 ax.quiver(*origin, *axis_direction, length=0.5, normalize=True)
#             else:
#                 ax.quiver(*origin, *axis_direction, length=1, normalize=True)
#         # Plot camera direction
#         z = -1 * rotation_matrix[:, 2]
#         ax.quiver(*origin, *z, length=1, normalize=True, color="r", alpha=0.5)

#     ax.set_xlabel("X")
#     ax.set_ylabel("Y")
#     ax.set_zlabel("Z")
#     ax.set_title("Multiple Cameras Extrinsics Visualization")

#     ax.set_zlim(-2, 2)

#     plt.show()


def pose_quat_to_se3(pos, quat):
    """
    Convert position and quaternion to SE3 transformation matrix.

    Parameters:
    - pos: Tuple of position (x, y, z)
    - quat: Tuple of quaternion (x, y, z, w)

    Returns:
    - SE3 transformation matrix as a numpy array
    """
    # Convert quaternion to rotation matrix
    rotation_matrix = R.from_quat(quat).as_matrix()

    # Create SE3 transformation matrix
    se3_matrix = np.eye(4)  # Initialize 4x4 identity matrix
    se3_matrix[:3, :3] = rotation_matrix  # Set rotation part
    se3_matrix[:3, 3] = pos  # Set translation part

    return se3_matrix


def find_closest_time(ref_time, times, start_index, tol_ms=10):
    tol_ns = tol_ms * 1000000

    diff_prev = abs(ref_time - times[start_index])
    is_found = False
    for i in range(start_index + 1, len(times)):
        diff = abs(ref_time - times[i])
        # If time difference start to increase, compare previous diff with tolerance
        if diff > diff_prev:
            # Returns true If previous diff is smaller than tolerance
            # Updates index as previous one in both cases for next search
            start_index = i - 1
            if diff_prev < tol_ns:
                is_found = True
            break

        diff_prev = diff

    return is_found, start_index


def variance_of_laplacian(image):
    return cv2.Laplacian(image, cv2.CV_64F).var()


def calc_sharpness(imagePath):
    image = cv2.imread(imagePath)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    fm = variance_of_laplacian(gray)
    return fm


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Open pose csv file and match images and poses, then make transformation.json file."
    )
    parser.add_argument(
        "--base_path",
        "-b",
        required=True,
        help="Path to the base dataset folder. Base folder should have images folder.",
    )
    parser.add_argument(
        "--calib_yaml",
        required=False,
        help="Path to the calibration file in YAML format. Default is base_path/calibration.yaml.",
    )
    parser.add_argument(
        "--image_folder",
        required=False,
        help="Path to the image(timestamp.png) folder. Default is base_path/images/",
    )
    parser.add_argument(
        "--pose_csv",
        required=False,
        help="Path to the camera pose csv(time, pos xyz, quat xyzw) file. Default is base_path/poses.csv",
    )

    args = parser.parse_args()
    base_path = args.base_path

    calib_yaml = args.calib_yaml or os.path.join(base_path, "calibration.yaml")
    if not os.path.isfile(calib_yaml):
        raise FileNotFoundError(f"Calibration file '{calib_yaml}' does not exist.")

    images_path = args.image_folder or os.path.join(base_path, "images")
    if not os.path.isdir(images_path):
        raise FileNotFoundError(f"Image folder '{images_path}' does not exist.")
    image_times = [int(f[:-4]) for f in os.listdir(images_path) if f.endswith(".png")]
    image_times.sort()

    poses_csv = args.pose_csv or os.path.join(base_path, "poses.csv")
    if not os.path.isfile(poses_csv):
        raise FileNotFoundError(f"Pose csv file '{poses_csv}' does not exist.")

    transformations = []

    csv_reads = pandas.read_csv(poses_csv)
    csv_index = 0
    num_missed = 0
    sampling_rate = 1

    # Plot original poses
    translations = np.empty((0, 3))
    eulers = np.empty((0, 3))
    for row in csv_reads.itertuples():
        pos = np.array([row.pos_x, row.pos_y, row.pos_z])
        translations = np.vstack([translations, pos])
        quat = np.array([row.quat_x, row.quat_y, row.quat_z, row.quat_w])
        # euler = R.from_quat(quat).as_euler("xyz", degrees=True)
        rot = R.from_quat(quat).as_matrix()
        euler = R.from_matrix(rot).as_euler("xyz", degrees=True)
        eulers = np.vstack([eulers, euler])
    plt.figure()
    plt.title("Translations")
    plt.subplot(3, 1, 1)
    plt.plot(translations[:, 0])
    plt.subplot(3, 1, 2)
    plt.plot(translations[:, 1])
    plt.subplot(3, 1, 3)
    plt.plot(translations[:, 2])

    plt.figure()
    plt.title("Eulrs")
    plt.subplot(3, 1, 1)
    plt.plot(eulers[:, 0])
    plt.subplot(3, 1, 2)
    plt.plot(eulers[:, 1])
    plt.subplot(3, 1, 3)
    plt.plot(eulers[:, 2])

    translations = np.empty((0, 3))
    eulers = np.empty((0, 3))
    print("Find closest pose for each image...")
    for i, image_time in enumerate(image_times):
        if i % sampling_rate != 0:
            continue

        is_found, csv_index = find_closest_time(
            image_time, csv_reads["timestamp"], csv_index
        )

        if not is_found:
            num_missed = num_missed + 1
            continue

        pos = csv_reads.loc[csv_index, ["pos_x", "pos_y", "pos_z"]].to_numpy()
        quat = csv_reads.loc[
            csv_index, ["quat_x", "quat_y", "quat_z", "quat_w"]
        ].to_numpy()

        se3 = pose_quat_to_se3(pos, quat)
        se3_w2w_prime = np.array([[0, -1, 0], [0, 0, -1], [1, 0, 0]])
        se3[0:3, 0:3] = se3_w2w_prime @ se3[0:3, 0:3]
        se3[0:3, 3] = se3_w2w_prime @ se3[0:3, 3]

        # for plotting
        translations = np.vstack([translations, se3[0:3, 3]])
        eulers = np.vstack(
            [eulers, R.from_matrix(se3[0:3, 0:3]).as_euler("zxy", degrees=True)]
        )

        transformations.append(
            {
                "timestamp": image_time,  # Include timestamp if needed for other uses
                "transform_matrix": se3.tolist(),  # Convert numpy array to list for JSON serialization
            }
        )

    plt.figure()
    plt.subplot(3, 1, 1)
    plt.plot(translations[:, 0])
    plt.subplot(3, 1, 2)
    plt.plot(translations[:, 1])
    plt.subplot(3, 1, 3)
    plt.plot(translations[:, 2])

    plt.figure()
    plt.title("Eulrs")
    plt.subplot(3, 1, 1)
    plt.plot(eulers[:, 0])
    plt.subplot(3, 1, 2)
    plt.plot(eulers[:, 1])
    plt.subplot(3, 1, 3)
    plt.plot(eulers[:, 2])

    plt.show()

    # if not os.path.exists(output_dir):
    #     raise FileNotFoundError(f"Output directory '{output_dir}' does not exist.")

    # output_file_path = os.path.join(os.path.abspath(output_dir), (args.topic + ".csv"))

    print(
        "#images not synced with poses / #total images:",
        num_missed,
        "/",
        len(image_times) / sampling_rate,
    )

    with open(calib_yaml, "r") as yaml_file:
        yaml_data = yaml.safe_load(yaml_file)

    camera_matrix = yaml_data["camera_matrix"]
    distortion_coeffs = yaml_data["distortion_coefficients"]

    json_data = {
        "camera_angle_x": 2
        * np.arctan(camera_matrix["data"][2] / camera_matrix["data"][0])
        * 180
        / np.pi,
        "camera_angle_y": 2
        * np.arctan(camera_matrix["data"][5] / camera_matrix["data"][4])
        * 180
        / np.pi,
        "fl_x": camera_matrix["data"][0],
        "fl_y": camera_matrix["data"][4],
        "k1": distortion_coeffs["data"][0],
        "k2": distortion_coeffs["data"][1],
        "p1": distortion_coeffs["data"][2],
        "p2": distortion_coeffs["data"][3],
        "cx": camera_matrix["data"][2],
        "cy": camera_matrix["data"][5],
        "w": yaml_data["image_width"],  # Replace with actual width
        "h": yaml_data["image_height"],  # Replace with actual height
        "aabb_scale": 32,
    }

    print("Adding frame data...")
    json_data["frames"] = []
    for i, transformation in enumerate(transformations):
        file_path = os.path.join("images", str(transformation["timestamp"]) + ".png")
        frame = {
            # "file_path": "images/" + str(transformation["timestamp"]) + ".png",
            "file_path": file_path,
            "sharpness": calc_sharpness(os.path.join(base_path, file_path)),
            "transform_matrix": transformation["transform_matrix"],
        }
        json_data["frames"].append(frame)
        print("Progress {} / {}".format(i + 1, len(transformations)), end="\r")

    print("\n")
    with open(os.path.join(base_path, "transforms.json"), "w") as outfile:
        json.dump(json_data, outfile, indent=2)

    print("Done!")
