# 天气查询工具集成说明

## 📌 概述

基于高德地图 Web 服务 API 实现的天气查询工具，支持查询全国各城市的实况天气和未来天气预报。

**API 文档**: https://lbs.amap.com/api/webservice/guide/api/weatherinfo

## 🔧 功能特性

### 1. 实况天气查询 (`get_weather`)

查询指定城市的当前天气状况，包括：
- 天气现象（晴、多云、雨等）
- 实时气温
- 风向风力
- 空气湿度
- 数据发布时间

**更新频率**: 每小时更新多次

### 2. 天气预报查询 (`get_weather` + `extensions="all"`)

查询指定城市未来3天的天气预报，包括：
- 每天的日期和星期
- 白天/夜间天气现象
- 白天/夜间温度
- 白天/夜间风向风力

**更新频率**: 每天更新3次（约在8点、11点、18点）

### 3. 便捷预报函数 (`get_weather_forecast`)

`get_weather` 的便捷版本，直接返回未来3天预报。

## 🚀 快速开始

### 1. 获取高德地图 API Key

1. 访问 [高德开放平台控制台](https://console.amap.com/)
2. 注册/登录账号
3. 创建应用
4. 申请 **Web 服务 API** 密钥（Key）
5. 复制 API Key

### 2. 配置环境变量

在 `.env` 文件中添加：

```bash
# 高德地图 API Key
AMAP_KEY=your-amap-api-key-here
```

### 3. 在代码中使用

```python
from core.tools.weather import get_weather, get_weather_forecast

# 查询北京实况天气
result = get_weather.invoke({"city": "北京", "extensions": "base"})
print(result)

# 查询上海未来3天预报
result = get_weather.invoke({"city": "上海", "extensions": "all"})
print(result)

# 使用便捷函数查询深圳预报
result = get_weather_forecast.invoke({"city": "深圳"})
print(result)
```

### 4. 在 Agent 中使用

```python
from core.tools import get_weather, get_weather_forecast
from agents.base_agent import create_base_agent

# 创建包含天气工具的 Agent
agent = create_base_agent(
    tools=[get_weather, get_weather_forecast],
    system_prompt="你是一个智能助手，可以查询天气信息。"
)

# 用户询问天气
response = agent.invoke({"messages": [{"role": "user", "content": "北京今天天气怎么样？"}]})
```

## 📖 API 参数说明

### `get_weather`

```python
def get_weather(
    city: str,
    extensions: Literal["base", "all"] = "base"
) -> str
```

**参数**:
- `city`: 城市名称或城市编码（adcode）
  - 支持城市名称：如 "北京"、"上海"、"深圳"
  - 支持 adcode：如 "110101"（北京东城区）
  - [城市编码表下载](https://lbs.amap.com/api/webservice/download)
  
- `extensions`: 气象类型
  - `"base"`: 返回实况天气（默认）
  - `"all"`: 返回预报天气

**返回**: 格式化的天气信息字符串

### `get_weather_forecast`

```python
def get_weather_forecast(city: str) -> str
```

**参数**:
- `city`: 城市名称或城市编码

**返回**: 格式化的天气预报信息字符串

## 📊 输出示例

### 实况天气输出

```
📍 地区：北京 北京市
🌤️ 天气：晴
🌡️ 温度：15°C
💨 风向：西北风
💨 风力：3级
💧 湿度：45%
⏰ 更新时间：2024-11-05 14:00:00
```

### 天气预报输出

```
📍 地区：广东 深圳市
⏰ 预报发布时间：2024-11-05 11:00:00

📅 2024-11-05 星期二
  🌞 白天：多云  28°C  东南风3级
  🌙 夜间：多云  22°C  东南风2级

📅 2024-11-06 星期三
  🌞 白天：阴  26°C  东风3级
  🌙 夜间：小雨  20°C  东风2级

📅 2024-11-07 星期四
  🌞 白天：小雨  24°C  东北风3级
  🌙 夜间：多云  19°C  东北风2级
```

## 🧪 测试工具

运行测试脚本验证集成：

```bash
# 确保已设置 AMAP_KEY 环境变量
cd backend
python scripts/test_weather.py
```

测试脚本会：
1. ✅ 检查 API Key 配置
2. 🌤️ 测试多个城市的实况天气查询
3. 🌤️ 测试多个城市的天气预报查询
4. 🌤️ 测试便捷预报函数
5. ⚠️ 测试错误处理（无效城市）

## ⚠️ 注意事项

1. **API Key 配置**
   - 必须在 `.env` 文件中设置 `AMAP_KEY`
   - 获取 Key: https://console.amap.com/

2. **数据更新频率**
   - 实况天气：每小时多次更新
   - 预报天气：每天3次更新（8点、11点、18点左右）
   - 以返回数据中的 `reporttime` 为准

3. **城市编码**
   - 支持使用城市名称（如 "北京"）
   - 支持使用 adcode（如 "110101"）
   - 使用 adcode 可以查询更精确的区域天气
   - [下载完整城市编码表](https://lbs.amap.com/api/webservice/download)

4. **错误处理**
   - API Key 未设置时会返回错误提示
   - 无效城市时会返回错误信息
   - 网络超时会自动提示重试

5. **使用限制**
   - 高德地图 API 有每日调用量限制
   - 个人开发者账号通常有较高的免费额度
   - 查看配额: https://console.amap.com/

## 🔗 相关链接

- **API 文档**: https://lbs.amap.com/api/webservice/guide/api/weatherinfo
- **控制台**: https://console.amap.com/
- **城市编码表**: https://lbs.amap.com/api/webservice/download
- **天气现象对照表**: https://lbs.amap.com/api/webservice/guide/tools/weather-code

## 📝 集成到项目

天气查询工具已集成到工具模块中：

```python
# backend/core/tools/__init__.py
from .weather import get_weather, get_weather_forecast

ADVANCED_TOOLS = [
    web_search,
    web_search_simple,
    get_weather,        # ✅ 天气查询
    get_weather_forecast,  # ✅ 天气预报
]
```

在创建 Agent 时，天气工具会自动包含在 `ALL_TOOLS` 或 `ADVANCED_TOOLS` 中：

```python
from core.tools import ALL_TOOLS
from agents.base_agent import create_base_agent

# 创建具备完整工具能力的 Agent
agent = create_base_agent(tools=ALL_TOOLS)
```

## ✅ 完成清单

- ✅ 实现 `get_weather` 工具
- ✅ 实现 `get_weather_forecast` 便捷函数
- ✅ 添加 `AMAP_KEY` 配置项到 `settings.py`
- ✅ 更新 `core/tools/__init__.py` 导出天气工具
- ✅ 创建测试脚本 `scripts/test_weather.py`
- ✅ 编写集成文档 `WEATHER_TOOL.md`
- ✅ 支持实况天气和预报天气两种模式
- ✅ 完善错误处理和日志记录
- ✅ 格式化输出，易于阅读

## 🎉 总结

天气查询工具已成功集成到 LC-StudyLab 项目中，现在 Agent 可以：

- 🌤️ 查询任意城市的实时天气状况
- 🌤️ 获取未来3天的天气预报
- 🌤️ 自动格式化输出，用户友好
- 🌤️ 处理各种错误情况

用户可以通过自然语言向 Agent 询问天气信息，例如：
- "北京今天天气怎么样？"
- "上海未来三天的天气预报"
- "深圳现在的温度是多少？"
- "广州这周会下雨吗？"

Agent 会自动调用相应的天气工具并返回格式化的结果！

