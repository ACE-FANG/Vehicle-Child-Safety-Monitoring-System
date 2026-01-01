# Vehicle Child Safety Monitoring System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![ESP32](https://img.shields.io/badge/platform-ESP32-orange)](https://www.espressif.com/)

## 项目概述

这是一个基于ESP32的**车载儿童安全监控系统**，旨在监测车内儿童遗留情况、环境指标（如温度、湿度、空气质量），并在检测到风险时自动触发警报、降窗通风或发送短信通知。系统包括硬件端（ESP32控制器和摄像头）、设备端Python程序（数据处理和AI检测）、远程控制端（监控界面）和网页端（移动访问）。

### 关键功能
- **实时监测**：温度、湿度、CO2、TVOC、物体温度、人体检测。
- **AI儿童识别**：使用YOLOv5模型检测儿童/成人，支持自动抓拍。
- **风险评估**：根据阈值判断警告/紧急状态，自动降窗或报警。
- **远程控制**：通过MQTT协议远程监控和控制（降窗、复位、测试报警）。
- **数据存储**：SQLite数据库记录传感器数据、检测结果和警报。
- **通知系统**：蜂鸣器、LED警灯、短信（via 4G模块）。
- **界面支持**：PyQt5图形界面、网页端（Flask）。

系统使用MQTT（broker.emqx.io）进行通信，支持模拟测试模式。

### 系统架构
- **硬件**：ESP32主控 + ESP32-CAM摄像头 + 传感器（HC-SR501 PIR、ENS160空气质量、AHT21温湿度、MLX90614红外温度、A3144霍尔门状态、ULN2003步进电机降窗、蜂鸣器、LED）。
- **软件**：Arduino IDE for ESP32代码，Python for 上位机和远程端。
- **通信**：WiFi、MQTT、串口备份。
- **AI**：YOLOv5模型（路径：`D:\设计代码程序\best (2).pt`）。

## 组件说明

### 硬件代码 (ESP32)
- `ESP32CAMv1.1.ino`: ESP32-CAM摄像头流媒体服务器（MJPEG流，优化延迟，支持VGA分辨率）。
- `ESP32v1.4.2.ino`: 主控制器代码，整合传感器读取、MQTT通信、电机控制、蜂鸣器报警、4G短信（英文）。

### Python代码
- `设备端v1.5.py`: 设备端主程序（整合通信、AI检测、GUI、数据库、短信报警、自动降窗）。
- `远控端v1.3.py`: 远程控制端（PyQt5界面，监控传感器数据、警报同步、抓拍图片显示）。
- `网页端.py`: 网页端服务器（Flask + SocketIO），提供移动端仪表盘和控制。

## 安装与运行

### 先决条件
- **硬件**：ESP32开发板、ESP32-CAM、传感器模块、4G模块（AIR780E）。
- **软件**：
  - Arduino IDE（上传ESP32代码）。
  - Python 3.8+。
  - 安装依赖：`pip install -r requirements.txt`。
- **MQTT Broker**：使用公共broker.emqx.io，或自建。

### 步骤
1. **克隆仓库**：
