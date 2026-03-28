# 智能天气查询快速体验

## 快速启动 CLI

```bash
cd backend
source venv/bin/activate
python scripts/demo_cli.py
```

## 体验场景

### 场景 1：智能时间识别

问 Agent：
```
👤 帮我查询一下明天深圳的天气
```

✅ Agent 会：
- 自动识别"明天"这个时间
- 只返回明天一天的天气（不是3-4天预报）
- 格式化显示：明天（日期 星期X）

### 场景 2：上下文记忆

连续对话：
```
👤 帮我查询一下明天深圳的天气
🤖 深圳明天（11月6日）天气多云，白天气温约28°C...

👤 后天呢？
🤖 深圳后天（11月7日）天气多云，白天预计26°C...
    ↑ 注意：Agent 自动记住了"深圳"

👤 那今天怎么样？
🤖 今天（11月5日）深圳天气晴朗...
    ↑ 继续记住了"深圳"
```

### 场景 3：不同城市切换

```
👤 明天北京天气怎么样？
🤖 [返回北京明天的天气]

👤 上海呢？
🤖 [返回上海明天的天气]
    ↑ 自动理解"上海"替换了"北京"
```

## 可用命令

在 CLI 中：
- `/help` - 查看帮助
- `/clear` - 清空对话历史（重新开始）
- `/info` - 查看当前配置
- `/quit` - 退出

## 运行测试

查看完整测试效果：
```bash
python scripts/test_weather_智能.py
```

测试包括：
1. 上下文记忆测试（连续3轮对话）
2. 单日查询准确性测试（3个不同城市）

## 技术原理

1. **`get_daily_weather` 工具**
   - 专门用于查询某一天的天气
   - 参数：`city`（城市），`day`（today/tomorrow/day_after_tomorrow）

2. **对话历史管理**
   - CLI 自动保存每轮对话的 HumanMessage 和 AIMessage
   - 传递给 Agent 的 `chat_history` 参数

3. **系统提示词指导**
   - 告诉模型如何使用天气工具
   - 指导模型从对话历史中提取城市和时间信息

## 常见问题

### Q: Agent 没有记住上下文怎么办？
A: 检查 `/info` 命令，确认对话历史不是空的。如果清空了，需要重新开始对话。

### Q: 为什么有时候返回多天预报？
A: 模型可能选择了 `get_weather_forecast` 工具而不是 `get_daily_weather`。这是正常的，模型会根据问题选择合适的工具。

### Q: 如何查询更远的时间（3天后、4天后）？
A: 目前 `get_daily_weather` 只支持今天/明天/后天。如需查询更远的时间，使用 `get_weather_forecast` 会返回未来3-4天的预报。

## 进阶功能

### 查看工具调用日志

日志文件：`backend/logs/app.log`

查看实时日志：
```bash
tail -f logs/app.log | grep "🌤️"
```

会看到类似：
```
🌤️ 查询天气: city=深圳, day=tomorrow (offset=1)
✅ 预报天气查询成功: 深圳市 明天
```

### 切换模型

编辑 `.env` 文件：
```bash
# 使用不同的模型
OPENAI_MODEL=gpt-4o        # 更智能，更贵
OPENAI_MODEL=gpt-4o-mini   # 便宜，但可能不够智能
OPENAI_MODEL=gpt-4.1-nano  # 最便宜
```

## 祝使用愉快！🎉

