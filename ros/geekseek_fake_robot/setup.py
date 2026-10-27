from glob import glob

from setuptools import find_packages, setup


package_name = "geekseek_fake_robot"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", ["launch/display.launch.py"]),
        (f"share/{package_name}/urdf", ["urdf/geekseek_fake_robot.urdf.xacro"]),
        (f"share/{package_name}/rviz", ["rviz/geekseek_fake_robot.rviz"]),
        (f"share/{package_name}/meshes", glob("meshes/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Geekseek",
    maintainer_email="geekseek@example.com",
    description="RViz fake robot driven by Geekseek semantic pose commands.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "fake_robot_node = geekseek_fake_robot.fake_robot_node:main",
        ],
    },
)
