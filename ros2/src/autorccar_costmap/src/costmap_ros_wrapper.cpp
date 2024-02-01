#include "costmap_ros_wrapper.h"

#include "costmap.h"
#include "rclcpp/rclcpp.hpp"

CostmapWrapper::CostmapWrapper(Costmap* pCostmap) : Node("costmap"), mpCostmap_(pCostmap) {
    point_cloud_subscriber_ = this->create_subscription<livox_ros_driver2::msg::CustomMsg>(
        "livox/lidar", 10, std::bind(&CostmapWrapper::PointCloudCallback, this, std::placeholders::_1));

    nav_state_subscriber_ = this->create_subscription<autorccar_interfaces::msg::NavState>(
        "/nav_topic", 10, std::bind(&CostmapWrapper::NavStateCallback, this, std::placeholders::_1));

    costmap_save_cmd_subscriber_ = this->create_subscription<std_msgs::msg::Bool>(
        "/costmap/save", 10, std::bind(&CostmapWrapper::CostmapSaveCmdCallback, this, std::placeholders::_1));

    global_occupancy_grid_publisher_ = this->create_publisher<nav_msgs::msg::OccupancyGrid>("occupancy_grid", 10);

    local_occupancy_grid_publisher_ = this->create_publisher<nav_msgs::msg::OccupancyGrid>("occupancy_grid/local", 10);

    // Initialization
    point_cloud_in_.reset(new pcl::PointCloud<pcl::PointXYZI>());
    point_cloud_in_->clear();
    transformation_.setIdentity();
}

void CostmapWrapper::PointCloudCallback(const livox_ros_driver2::msg::CustomMsg& msg) {
    // Convert livox msg to pcl msg
    point_cloud_in_->clear();  // Removes all points in a cloud and sets the width and height to 0
    point_cloud_in_->reserve(msg.point_num);
    point_cloud_in_->header.frame_id = msg.header.frame_id;
    point_cloud_in_->header.stamp = (uint64_t)((msg.header.stamp.sec * 1e9 + msg.header.stamp.nanosec) / 1000);

    pcl::PointXYZI point;
    for (uint i = 0; i < msg.point_num - 1; i++) {
        point.x = msg.points[i].x;
        point.y = msg.points[i].y;
        point.z = msg.points[i].z;
        point.intensity = msg.points[i].reflectivity;
        point_cloud_in_->push_back(point);
    }

    // Update pointcloud
    mpCostmap_->UpdatePointCloud(point_cloud_in_);

    // Update global costmap & publish occupancy grid
    if (mpCostmap_->costmap_flag_) {
        mpCostmap_->UpdateCostmap();
        PublishGlobalOccupancyGrid(false);
        PublishLocalOccupancyGrid();
    }
}

void CostmapWrapper::NavStateCallback(const autorccar_interfaces::msg::NavState& msg) {
    transformation_.block<3, 3>(0, 0) =
        Eigen::Quaterniond(msg.quaternion.w, msg.quaternion.x, msg.quaternion.y, msg.quaternion.z).toRotationMatrix();
    transformation_(0, 3) = msg.position.x;
    transformation_(1, 3) = msg.position.y;
    transformation_(2, 3) = msg.position.z;

    // Update pose
    mpCostmap_->UpdatePose(transformation_);
}

void CostmapWrapper::CostmapSaveCmdCallback(const std_msgs::msg::Bool& msg) {
    if (msg.data) {
        PublishGlobalOccupancyGrid(true);
    }
}

void CostmapWrapper::PublishGlobalOccupancyGrid(bool save_pgm) {
    CostmapInfo info = mpCostmap_->GetGlobalCostmapInfo();

    nav_msgs::msg::OccupancyGrid occupancy_grid_map;
    occupancy_grid_map.header.stamp = this->get_clock()->now();
    occupancy_grid_map.header.frame_id = "map";
    occupancy_grid_map.info.width = info.size_x;
    occupancy_grid_map.info.height = info.size_y;
    occupancy_grid_map.info.resolution = static_cast<float>(info.resolution);
    occupancy_grid_map.info.origin.position.x = static_cast<float>(info.origin_pos_x);
    occupancy_grid_map.info.origin.position.y = static_cast<float>(info.origin_pos_y);

    for (size_t i = 0; i < info.size_x * info.size_y; i++) {
        // Convert log odds to probability(0~1)
        double logOdds = info.costmap->data()[i];
        double prob = std::exp(logOdds) / (1 + std::exp(logOdds));

        if (prob == 0.5) {
            occupancy_grid_map.data.push_back(-1);
        } else {
            occupancy_grid_map.data.push_back(prob * 100);
        }
    }

    global_occupancy_grid_publisher_->publish(occupancy_grid_map);

    if (save_pgm) {
        SaveCostmapAsPgm(occupancy_grid_map);
    }
}

void CostmapWrapper::PublishLocalOccupancyGrid() {
    CostmapInfo info = mpCostmap_->GetLocalCostmapInfo();

    nav_msgs::msg::OccupancyGrid occupancy_grid_map;
    occupancy_grid_map.header.stamp = this->get_clock()->now();
    occupancy_grid_map.header.frame_id = "map";
    occupancy_grid_map.info.width = info.size_x;
    occupancy_grid_map.info.height = info.size_y;
    occupancy_grid_map.info.resolution = static_cast<float>(info.resolution);
    occupancy_grid_map.info.origin.position.x = static_cast<float>(info.origin_pos_x);
    occupancy_grid_map.info.origin.position.y = static_cast<float>(info.origin_pos_y);

    for (size_t i = 0; i < info.size_x * info.size_y; i++) {
        // convert log odds to probability(0~1)
        double logOdds = info.costmap->data()[i];
        double prob = std::exp(logOdds) / (1 + std::exp(logOdds));

        if (prob == 0.5) {
            occupancy_grid_map.data.push_back(-1);
        } else {
            occupancy_grid_map.data.push_back(prob * 100);
        }
    }

    local_occupancy_grid_publisher_->publish(occupancy_grid_map);
}

void CostmapWrapper::SaveCostmapAsPgm(const nav_msgs::msg::OccupancyGrid& msg) {
    // save as pgm (https://github.com/ros-planning/navigation/blob/noetic-devel/map_server/src/map_saver.cpp)
    std::string package_share_dir = ament_index_cpp::get_package_share_directory("autorccar_costmap");
    std::string map_data_file = package_share_dir + "/map.pgm";

    FILE* out = fopen(map_data_file.c_str(), "w");
    if (!out) {
        RCLCPP_INFO(this->get_logger(), "Couldn't save map file");
        return;
    }

    fprintf(out, "P5\n# CREATOR: map_saver.cpp %.3f m/pix\n%d %d\n255\n", msg.info.resolution, msg.info.width,
            msg.info.height);

    for (unsigned int y = 0; y < msg.info.height; y++) {
        for (unsigned int x = 0; x < msg.info.width; x++) {
            unsigned int i = x + (msg.info.height - y - 1) * msg.info.width;
            if (msg.data[i] >= 0 && msg.data[i] <= 25) {  // [0,free)
                fputc(254, out);
            } else if (msg.data[i] >= 65) {  // (occ,255]
                fputc(000, out);
            } else {  // occ [0.25,0.65]
                fputc(205, out);
            }
        }
    }

    fclose(out);

    RCLCPP_INFO(this->get_logger(), "Saved map occupancy data to %s", map_data_file.c_str());
}
