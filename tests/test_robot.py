import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from geekseek.robot import FakeRobot, pose_for_template


def _fake_ros_modules() -> dict[str, MagicMock]:
    """rclpy isn't importable in every dev/test environment (its C extension
    is built for a specific Python version), so RvizRobot's `import rclpy`
    is faked out via sys.modules rather than requiring the real package."""
    fake_rclpy = MagicMock()
    fake_rclpy.ok.return_value = False
    fake_node = MagicMock()
    fake_rclpy.create_node.return_value = fake_node
    fake_publisher = MagicMock()
    fake_publisher.get_subscription_count.return_value = 1
    fake_node.create_publisher.return_value = fake_publisher
    fake_node.count_publishers.return_value = 1
    fake_executors = MagicMock()
    fake_std_msgs_msg = MagicMock()
    fake_std_msgs_msg.String = lambda data: SimpleNamespace(data=data)
    return {
        "rclpy": fake_rclpy,
        "rclpy.executors": fake_executors,
        "std_msgs": MagicMock(),
        "std_msgs.msg": fake_std_msgs_msg,
    }


class RobotTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_robot_records_semantic_pose(self) -> None:
        robot = FakeRobot(0)
        await robot.move_to(pose_for_template("upper_body"))
        self.assertEqual(robot.moves, ["frame.upper_body"])

    async def test_rviz_robot_completes_when_status_arrives(self) -> None:
        from geekseek.robot import RvizRobot

        modules = _fake_ros_modules()
        with patch.dict(sys.modules, modules):
            robot = RvizRobot(move_seconds=0, min_status_timeout=2.0)

            move_task = asyncio.create_task(robot.move_to("frame.full_body"))
            await asyncio.sleep(0)  # let move_to publish and start waiting
            robot._on_status(SimpleNamespace(data="completed:frame.full_body"))
            await move_task

        self.assertEqual(robot.moves, ["frame.full_body"])
        published = robot._target_publisher.publish.call_args.args[0]
        self.assertEqual(published.data, "frame.full_body")

    async def test_rviz_robot_ignores_status_for_a_different_pose(self) -> None:
        from geekseek.robot import RvizRobot

        modules = _fake_ros_modules()
        with patch.dict(sys.modules, modules):
            robot = RvizRobot(move_seconds=0, min_status_timeout=0.05)

            move_task = asyncio.create_task(robot.move_to("frame.full_body"))
            await asyncio.sleep(0)
            robot._on_status(SimpleNamespace(data="completed:frame.upper_body"))

            with self.assertRaises(RuntimeError):
                await move_task

    async def test_rviz_robot_raises_when_status_never_arrives(self) -> None:
        from geekseek.robot import RvizRobot

        modules = _fake_ros_modules()
        with patch.dict(sys.modules, modules):
            robot = RvizRobot(move_seconds=0, min_status_timeout=0.05)

            with self.assertRaises(RuntimeError):
                await robot.move_to("frame.full_body")

    async def test_rviz_robot_bootstraps_ros_sys_path(self) -> None:
        from geekseek.robot import RvizRobot

        original_sys_path = list(sys.path)
        modules = _fake_ros_modules()
        try:
            with patch.dict(sys.modules, modules):
                RvizRobot(move_seconds=0, min_status_timeout=0.05)
            self.assertTrue(any(p in sys.path for p in RvizRobot._ROS_SITE_PACKAGES))
        finally:
            sys.path[:] = original_sys_path
