from __future__ import annotations

import math
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from visualization_msgs.msg import Marker


JOINT_NAMES = [
    "base_yaw_joint",
    "shoulder_pitch_joint",
    "elbow_pitch_joint",
    "wrist_pitch_joint",
    "end_effector_yaw_joint",
]

POSES = {
    "home": (0, 0, 0, 0, 0),
    "frame.full_body": (0, -10, 20, -10, 0),
    "frame.upper_body": (-15, -20, 35, -15, 10),
    "frame.product_closeup": (20, -30, 50, -25, -10),
    "safe.retreat": (0, -25, 40, -15, 0),
}


class FakeRobotNode(Node):
    def __init__(self) -> None:
        super().__init__("geekseek_fake_robot")
        self.declare_parameter("move_seconds", 1.2)
        self.move_seconds = float(self.get_parameter("move_seconds").value)
        self.publisher = self.create_publisher(JointState, "/joint_states", 10)
        self.status_publisher = self.create_publisher(String, "/geekseek/fake_robot/status", 10)
        self.marker_publisher = self.create_publisher(
            Marker, "/geekseek/fake_robot/debug_marker", 10
        )
        self.create_subscription(String, "/geekseek/fake_robot/target", self.on_target, 10)
        self.timer = self.create_timer(1.0 / 30.0, self.tick)

        self.start = self._radians(POSES["home"])
        self.current = list(self.start)
        self.target = list(self.start)
        self.started_at = time.monotonic()
        self.active_pose = "home"
        self.completed = True
        self.get_logger().info("Ready; publish semantic poses on /geekseek/fake_robot/target")

    @staticmethod
    def _radians(values: tuple[int, ...]) -> list[float]:
        return [math.radians(value) for value in values]

    def on_target(self, message: String) -> None:
        if message.data not in POSES:
            self.get_logger().warning(f"Unknown pose: {message.data}")
            return
        self.start = list(self.current)
        self.target = self._radians(POSES[message.data])
        self.started_at = time.monotonic()
        self.active_pose = message.data
        self.completed = False
        self.status_publisher.publish(String(data=f"moving:{self.active_pose}"))

    def tick(self) -> None:
        elapsed = time.monotonic() - self.started_at
        ratio = min(1.0, elapsed / max(self.move_seconds, 0.001))
        smooth = ratio * ratio * (3.0 - 2.0 * ratio)
        self.current = [
            start + (target - start) * smooth for start, target in zip(self.start, self.target)
        ]
        message = JointState()
        message.header.stamp = self.get_clock().now().to_msg()
        message.name = JOINT_NAMES
        message.position = self.current
        self.publisher.publish(message)
        self.publish_debug_markers(message.header.stamp)

        if ratio >= 1.0 and not self.completed:
            self.completed = True
            self.status_publisher.publish(String(data=f"completed:{self.active_pose}"))

    def publish_debug_markers(self, stamp) -> None:
        camera = Marker()
        camera.header.frame_id = "end_effector_link"
        camera.header.stamp = stamp
        camera.ns = "camera_direction"
        camera.id = 0
        camera.type = Marker.ARROW
        camera.action = Marker.ADD
        camera.pose.orientation.w = 1.0
        camera.scale.x = 0.24
        camera.scale.y = 0.025
        camera.scale.z = 0.025
        camera.color.r = 0.3
        camera.color.g = 0.9
        camera.color.b = 1.0
        camera.color.a = 0.9
        self.marker_publisher.publish(camera)

        label = Marker()
        label.header.frame_id = "base_link"
        label.header.stamp = stamp
        label.ns = "active_pose"
        label.id = 1
        label.type = Marker.TEXT_VIEW_FACING
        label.action = Marker.ADD
        label.pose.position.z = 0.55
        label.pose.orientation.w = 1.0
        label.scale.z = 0.045
        label.color.r = 1.0
        label.color.g = 1.0 if self.completed else 0.65
        label.color.b = 1.0 if self.completed else 0.15
        label.color.a = 1.0
        phase = "READY" if self.completed else "MOVING"
        label.text = f"{phase} · {self.active_pose}"
        self.marker_publisher.publish(label)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FakeRobotNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
