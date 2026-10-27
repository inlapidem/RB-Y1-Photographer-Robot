from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
import os


def generate_launch_description() -> LaunchDescription:
    package_dir = get_package_share_directory("geekseek_fake_robot")
    xacro_file = os.path.join(package_dir, "urdf", "geekseek_fake_robot.urdf.xacro")
    rviz_file = os.path.join(package_dir, "rviz", "geekseek_fake_robot.rviz")
    robot_description = ParameterValue(Command(["xacro ", xacro_file]), value_type=str)

    return LaunchDescription(
        [
            DeclareLaunchArgument("move_seconds", default_value="1.2"),
            DeclareLaunchArgument("use_fake_robot", default_value="true"),
            DeclareLaunchArgument("use_rviz", default_value="true"),
            Node(
                package="robot_state_publisher",
                executable="robot_state_publisher",
                parameters=[{"robot_description": robot_description}],
            ),
            Node(
                package="geekseek_fake_robot",
                executable="fake_robot_node",
                parameters=[
                    {
                        "move_seconds": ParameterValue(
                            LaunchConfiguration("move_seconds"), value_type=float
                        )
                    }
                ],
                condition=IfCondition(LaunchConfiguration("use_fake_robot")),
                output="screen",
            ),
            Node(
                package="rviz2",
                executable="rviz2",
                arguments=["-d", rviz_file],
                condition=IfCondition(LaunchConfiguration("use_rviz")),
                output="screen",
            ),
        ]
    )
