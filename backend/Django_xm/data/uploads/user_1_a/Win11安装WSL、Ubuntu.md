 

1\. 服务器已安装 Docker环境，最低版本号需使用 20.10.0，推荐 20.10.13
------------------------------------------------

在 Windows 上使用Docker Desktop的前提：依赖WSL2作为运行环境。需要先安装WSL2并配置一个linux。WSL可直接在Windows上运行Linux而不需要虚拟、WSL2为WSL的升级版，WSL2是windows提供的轻量级LINUX运行环境，具备完整的LINUX内核。

下面主要从两个大步骤安装Docker Desktop:

### 1.1. WSL2的安装

#### **1.1.1 PowerShell 命令行快速安装 WSL2**

搜索 PowerShell —》 右键 Windows PowerShell —》 选择管理员身份运行

![](./assets/ebf2b2e4fcb048f19dc5eeeb0ec5bccb.png)

在 PowerShell（管理员模式）中运行：

> dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

### ![](./assets/98fdcb47da374864a32f0344c4f75061.png)

#### 1.1.2 启用虚拟机平台功能

命令行 运行：

> dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

### ![](./assets/ee8c9a52ef5e46bc9d9709648cc277cb.png)

#### 1.1.3 将 WSL 默认版本设置为 WSL2

命令行运行：

> wsl --set-default-version 2

#### 提示我不是最新版本，需要update，默认update操作，提示与服务器的连接被重置

搜了很多方法:

###### 1.下载手动安装包（提示我This update only applies to machines with the WindowsSubsytem for Linux）；

###### 2.执行脚本更新；

> pushd "%~dp0"  
> dir /b %SystemRoot%\\servicing\\Packages\\\*Hyper-V\*.mum >hyper-v.txt  
> for /f %%i in ('findstr /i . hyper-v.txt 2^>nul') do dism /online /norestart /add-package:"%SystemRoot%\\servicing\\Packages\\%%i"  
> del hyper-v.txt  
> Dism /online /enable-feature /featurename:Microsoft-Hyper-V-All /LimitAccess /ALL

###### 3.切换网络为个人热点，成功执行，应该是内网限制了

安装完成之后切换版本

![](./assets/a1585b015cbb4ff884a496ef6a1f7985.png)

### 1.2 安装 Ubuntu \-20.04 至D盘

#### `1.D盘` 创建 **WSL**  文件夹 ，并在该文件夹下创建 **Ubuntu-20.04** 文件夹

![](./assets/dd02fe86bdb24bc183399b54f3fdff10.png)

执行命令：

> wsl --install -d Ubuntu-20.04

![](./assets/951757e540354b07a42510a3a835a8a6.png)

安装完毕后会要求你创建一个新用户，按照提示输入用户名和密码即可

### ![](./assets/211e2f1b49c24310a17d702f5b1a0344.png)

ctrl+D退出登录

#### 2.导出 **Ubuntu-20.04** 为 `.tar` 文件并移动到D盘文件夹

执行命令行：

> wsl --export Ubuntu-20.04 D:\\WSL\\Ubuntu-20.04\\Ubuntu-20.04.tar

### ![](./assets/e3f0ed9204224f30b55b08383646ac10.png)

#### 3.取消注册原有的 Ubuntu-20.04，（注销默认安装在 `C` 盘的），可以将其从 WSL 注销

执行命令：

> wsl --unregister Ubuntu-20.04

#### 4.**导入 Ubuntu-20.04 到 D 盘**

运行以下命令，将 Ubuntu-20.04 重新导入到 `D:\WSL\Ubuntu-20.04`：

> wsl --import Ubuntu-20.04 D:\\WSL\\Ubuntu-20.04 D:\\WSL\\Ubuntu-20.04\\Ubuntu-20.04.tar --version 2

### ![](./assets/12251bdfa46e4c68821aa1b9a1a31774.png)

5.在 `D:\WSL\Ubuntu-20.04` 目录下，WSL2 发行版的文件存储在一个 **虚拟磁盘映像文件（ext4.vhdx）** 中，该文件用于存储整个 Ubuntu-20.04 文件系统 ，如下图所示：

![](./assets/3de5970c2ca349eaae9a0e2cc8cc7571.png)

### 1.3 启动Ubuntu并设置普通用户

##### 1.执行命令行：

> wsl -d Ubuntu-20.04

##### 2.创建新用户（可以直接使用安装时创建的用户），执行命令：

> adduser yourusername（换成自己起的名字）

##### 3.新用户赋权，就可以使用 `sudo` 进行管理员操作。执行命令：

> usermod -aG sudo yourusername

### ![](./assets/435708563f294744b39857689a5401cc.png)

##### 4.修改默认登录用户为普通用户（可选）

执行命令：

> ubuntu2004.exe config --default-user yourusername

### 1.4 **确认安装成功**

执行命令：

> wsl -l -v

### ![](./assets/ceeb7a5e8aa44351a3c030b6f8e870fc.png)

### 1.5  在完成了WSL2的安装，并且能够在WSL终端中正常运行 Linux命令 后，再继续进行Docker Desktop的安装配置

#### 1\. 下载 Docker Desktop：

访问 Docker 官网：[Docker: Accelerated Container Application Development](https://www.docker.com/ "Docker: Accelerated Container Application Development")  
点击页面上的“Download for Windows - AMD64”按钮，以下载适用于 Windows 系统的 Docker Desktop 安装文件

![](./assets/6a670658f4ba45a180447d73d6cdbd0c.png)

双击下载的安装文件，开始安装 Docker Desktop。按照安装向导的指示完成安装。

#### 2.打开Docker Desktop，点击Accept，然后跳过登录![](./assets/18fffd976d324f15b9065905a241ec70.png)

左下角回显示docker的状态，为engine running为启动状态，若非这个状态则点击左下角的三点设置重启即可。

![](./assets/b6b0e268a9d2438bb7bae55ba159628e.png)

本文转自 <https://blog.csdn.net/wqw0124/article/details/154947869>，如有侵权，请联系删除。