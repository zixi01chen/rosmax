from setuptools import setup

package_name = "rosmax_orchestrator"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    # MVP：极简 sweep 驱动。状态机/WorkflowEngine 延后 W3（见 CEO plan）
    entry_points={
        "console_scripts": [
            "sweep_driver = rosmax_orchestrator.sweep_driver:main",
        ],
    },
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="zixi01.chen",
    maintainer_email="zixi01.chen@horizonrobotics.com",
    description="rosmax 编排器：MVP 为参数化 sweep 驱动，W3 升级为状态机编排",
    license="Proprietary",
)
