import httpx
import re
from datetime import datetime, timedelta
from typing import Optional, Literal
from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)

_CITY_AD_CODES = {
    "北京": "110000", "天津": "120000", "上海": "310000", "重庆": "500000",
    "石家庄": "130100", "太原": "140100", "沈阳": "210100", "长春": "220100",
    "哈尔滨": "230100", "南京": "320100", "杭州": "330100", "合肥": "340100",
    "福州": "350100", "南昌": "360100", "济南": "370100", "郑州": "410100",
    "武汉": "420100", "长沙": "430100", "广州": "440100", "南宁": "450100",
    "海口": "460100", "成都": "510100", "贵阳": "520100", "昆明": "530100",
    "拉萨": "540100", "西安": "610100", "兰州": "620100", "西宁": "630100",
    "银川": "640100", "乌鲁木齐": "650100", "呼和浩特": "150100",
    "泸州": "510500", "绵阳": "510700", "德阳": "510600", "宜宾": "511500",
    "乐山": "511100", "南充": "511300", "达州": "511700", "自贡": "510300",
    "攀枝花": "510400", "遂宁": "510900", "内江": "511000", "广元": "510800",
    "苏州": "320500", "无锡": "320200", "常州": "320400", "徐州": "320300",
    "南通": "320600", "扬州": "321000", "盐城": "320900", "镇江": "321100",
    "泰州": "321200", "温州": "330300", "宁波": "330200", "嘉兴": "330400",
    "湖州": "330500", "绍兴": "330600", "金华": "330700", "台州": "331000",
    "东莞": "441900", "佛山": "440600", "珠海": "440400", "惠州": "441300",
    "中山": "442000", "汕头": "440500", "江门": "440700", "湛江": "440800",
    "深圳": "440300", "厦门": "350200", "泉州": "350500", "漳州": "350600",
    "烟台": "370600", "潍坊": "370700", "临沂": "371300", "济宁": "370800",
    "淄博": "370300", "威海": "371000", "大连": "210200", "鞍山": "210300",
    "吉林": "220200", "齐齐哈尔": "230200", "大理": "532901", "丽江": "530700",
    "桂林": "450300", "三亚": "460200", "洛阳": "410300", "开封": "410200",
    "保定": "130600", "唐山": "130200", "秦皇岛": "130300", "邯郸": "130400",
    "襄阳": "420600", "宜昌": "420500", "岳阳": "430600", "常德": "430700",
    "株洲": "430200", "衡阳": "430400", "遵义": "520300", "曲靖": "530300",
    "咸阳": "610400", "宝鸡": "610300", "天水": "620500",
}

_CITY_COORDS = {
    "北京": (39.9042, 116.4074), "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644), "深圳": (22.5431, 114.0579),
    "成都": (30.5728, 104.0668), "杭州": (30.2741, 120.1551),
    "武汉": (30.5928, 114.3055), "南京": (32.0603, 118.7969),
    "重庆": (29.4316, 106.9123), "西安": (34.3416, 108.9398),
    "长沙": (28.2282, 112.9388), "天津": (39.3434, 117.3616),
    "苏州": (31.2990, 120.5853), "郑州": (34.7466, 113.6254),
    "青岛": (36.0671, 120.3826), "大连": (38.9140, 121.6147),
    "厦门": (24.4798, 118.0894), "昆明": (25.0389, 102.7183),
    "济南": (36.6512, 116.9972), "合肥": (31.8206, 117.2272),
    "福州": (26.0745, 119.2965), "南宁": (22.8170, 108.3665),
    "贵阳": (26.6470, 106.6302), "兰州": (36.0611, 103.8343),
    "太原": (37.8706, 112.5489), "沈阳": (41.8057, 123.4315),
    "长春": (43.8171, 125.3235), "哈尔滨": (45.8038, 126.5350),
    "海口": (20.0440, 110.1999), "石家庄": (38.0428, 114.5149),
    "呼和浩特": (40.8424, 111.7491), "乌鲁木齐": (43.7930, 87.6271),
    "拉萨": (29.6500, 91.1000), "西宁": (36.6171, 101.7782),
    "银川": (38.4872, 106.2309), "泸州": (28.8717, 105.4423),
    "绵阳": (31.4680, 104.6796), "宜宾": (28.7513, 104.6417),
    "乐山": (29.5521, 103.7662), "南充": (30.8373, 106.1107),
    "珠海": (22.2710, 113.5767), "东莞": (23.0208, 113.7518),
    "佛山": (23.0218, 113.1219), "温州": (28.0001, 120.6722),
    "宁波": (29.8683, 121.5440), "无锡": (31.4912, 120.3119),
    "烟台": (37.4638, 121.4479), "三亚": (18.2528, 109.5120),
    "桂林": (25.2744, 110.2990), "洛阳": (34.6197, 112.4540),
}


def _normalize_city_name(city: str) -> tuple:
    if not city:
        return (city, city)
    original = city
    city = city.strip()
    city = re.sub(r'(今天|明天|后天|现在|当前|的|天气|情况|预报|查询|请问|帮我|查一下)', '', city)
    city = city.strip()
    for province in ["四川省", "广东省", "浙江省", "江苏省", "山东省", "河南省",
                     "湖北省", "湖南省", "福建省", "安徽省", "河北省", "山西省",
                     "陕西省", "甘肃省", "青海省", "辽宁省", "吉林省", "黑龙江省",
                     "贵州省", "云南省", "海南省", "台湾省", "内蒙古", "广西",
                     "西藏", "宁夏", "新疆", "北京", "上海", "天津", "重庆",
                     "四川", "广东", "浙江", "江苏", "山东", "河南", "湖北",
                     "湖南", "福建", "安徽", "河北", "山西", "陕西", "甘肃",
                     "青海", "辽宁", "吉林", "黑龙江", "贵州", "云南", "海南"]:
        if city.startswith(province):
            city = city[len(province):]
            break
    city = city.rstrip('市区县')
    if city in _CITY_AD_CODES:
        return (_CITY_AD_CODES[city], city)
    return (city, city)


def get_amap_key() -> Optional[str]:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'amap_key', None)
    except ImportError:
        import os
        return os.environ.get("AMAP_KEY")


def _get_weather_impl(
    city: str,
    extensions: Literal["base", "all"] = "base"
) -> str:
    normalized_city, city_name = _normalize_city_name(city)
    logger.info(f"查询天气: city={city} -> adcode={normalized_city}, city_name={city_name}, extensions={extensions}")

    amap_key = get_amap_key()
    if amap_key:
        result = _query_amap(amap_key, normalized_city, city, extensions)
        if result and not result.startswith("错误："):
            return result
        logger.warning(f"高德天气查询失败，尝试 Open-Meteo 兜底: {result}")

    return _query_open_meteo(city_name or city, extensions)


def _query_amap(amap_key: str, normalized_city: str, original_city: str, extensions: str) -> str:
    url = "https://restapi.amap.com/v3/weather/weatherInfo"

    for attempt_city in [normalized_city, original_city]:
        params = {
            "key": amap_key,
            "city": attempt_city,
            "extensions": extensions,
            "output": "JSON"
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            if data.get("status") != "1":
                info = data.get("info", "未知错误")
                if info == "INVALID_USER_IP":
                    return f"错误：高德 API IP 白名单限制，请检查 AMAP_KEY 配置 (city={attempt_city})"
                if attempt_city == normalized_city and normalized_city != original_city:
                    logger.warning(f"标准化城市名 '{normalized_city}' 查询失败，尝试原始名称 '{original_city}'")
                    continue
                return f"错误：天气查询失败: {info} (city={attempt_city})"

            if extensions == "base":
                return _format_live_weather(data)
            else:
                return _format_forecast_weather(data)

        except httpx.TimeoutException:
            return "错误：天气查询超时，请稍后重试"
        except httpx.HTTPStatusError as e:
            return f"错误：HTTP 请求失败: {e.response.status_code}"
        except Exception as e:
            return f"错误：天气查询异常: {str(e)}"

    return f"错误：无法查询 {original_city} 的天气信息"


def _query_open_meteo(city_name: str, extensions: str) -> str:
    coords = _CITY_COORDS.get(city_name)
    if not coords:
        return f"错误：无法查询 {city_name} 的天气信息（高德 API 不可用且城市坐标未收录），请尝试使用标准城市名"

    lat, lon = coords
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m",
            "timezone": "Asia/Shanghai",
        }
        if extensions == "all":
            params["daily"] = "weather_code,temperature_2m_max,temperature_2m_min,wind_speed_10m_max"
            params["forecast_days"] = 4

        with httpx.Client(timeout=10.0) as client:
            response = client.get("https://api.open-meteo.com/v1/forecast", params=params)
            response.raise_for_status()
            data = response.json()

        return _format_open_meteo(city_name, data, extensions)

    except httpx.TimeoutException:
        return "错误：天气查询超时，请稍后重试"
    except Exception as e:
        return f"错误：天气查询异常: {str(e)}"


_WMO_CODES = {
    0: "晴", 1: "大部晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇", 51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "大冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "大阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷暴", 96: "雷暴冰雹", 99: "强雷暴冰雹",
}

_WIND_DIRS = ["北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风"]


def _format_open_meteo(city_name: str, data: dict, extensions: str) -> str:
    current = data.get("current", {})
    temp = current.get("temperature_2m", "未知")
    humidity = current.get("relative_humidity_2m", "未知")
    weather_code = current.get("weather_code", 0)
    weather = _WMO_CODES.get(weather_code, "未知")
    wind_speed = current.get("wind_speed_10m", "未知")
    wind_dir_deg = current.get("wind_direction_10m", 0)
    wind_dir = _WIND_DIRS[round(wind_dir_deg / 45) % 8] if isinstance(wind_dir_deg, (int, float)) else "未知"

    result = (
        f"📍 {city_name}\n"
        f"🌡️ 当前温度: {temp}°C\n"
        f"🌤️ 天气状况: {weather}\n"
        f"💨 风向: {wind_dir} ({wind_speed} km/h)\n"
        f"💧 湿度: {humidity}%\n"
        f"📡 数据来源: Open-Meteo"
    )

    if extensions == "all" and "daily" in data:
        daily = data["daily"]
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])

        day_names = ["今天", "明天", "后天"]
        forecast_lines = []
        for i in range(min(len(dates), 4)):
            dn = day_names[i] if i < len(day_names) else f"第{i+1}天"
            dt = dates[i] if i < len(dates) else ""
            hi = max_temps[i] if i < len(max_temps) else "未知"
            lo = min_temps[i] if i < len(min_temps) else "未知"
            wc = _WMO_CODES.get(codes[i], "未知") if i < len(codes) else "未知"
            forecast_lines.append(
                f"\n{dn} ({dt}):\n  🌤️ {wc}, {lo}°C ~ {hi}°C"
            )
        result += "\n" + "".join(forecast_lines)

    return result


def _format_live_weather(data: dict) -> str:
    weather_info = data.get("lives", [{}])[0]
    if not weather_info:
        return "未获取到天气信息"

    city = weather_info.get("city", "未知")
    weather = weather_info.get("weather", "未知")
    temperature = weather_info.get("temperature", "未知")
    wind_direction = weather_info.get("winddirection", "未知")
    wind_power = weather_info.get("windpower", "未知")
    humidity = weather_info.get("humidity", "未知")
    report_time = weather_info.get("reporttime", "未知")

    return (
        f"📍 {city}\n"
        f"🌡️ 当前温度: {temperature}°C\n"
        f"🌤️ 天气状况: {weather}\n"
        f"💨 风向: {wind_direction}风 ({wind_power}级)\n"
        f"💧 湿度: {humidity}%\n"
        f"🕐 发布时间: {report_time}"
    )


def _format_forecast_weather(data: dict) -> str:
    forecasts = data.get("forecasts", [{}])
    if not forecasts:
        return "未获取到天气预报信息"

    forecast_info = forecasts[0]
    city = forecast_info.get("city", "未知")
    report_time = forecast_info.get("reporttime", "未知")
    casts = forecast_info.get("casts", [])

    result = [f"📍 {city} 天气预报 (发布时间: {report_time})\n"]

    day_names = ["今天", "明天", "后天"]

    for i, cast in enumerate(casts[:4]):
        if i < len(day_names):
            day_name = day_names[i]
        else:
            day_name = f"第{i+1}天"

        date = cast.get("date", "")
        day_weather = cast.get("dayweather", "未知")
        night_weather = cast.get("nightweather", "未知")
        day_temp = cast.get("daytemp", "未知")
        night_temp = cast.get("nighttemp", "未知")
        day_wind = cast.get("daywind", "未知")
        night_wind = cast.get("nightwind", "未知")
        day_power = cast.get("daypower", "未知")
        night_power = cast.get("nightpower", "未知")

        result.append(
            f"\n{day_name} ({date}):\n"
            f"  🌤️ 白天: {day_weather}, {day_temp}°C, {day_wind}风 {day_power}级\n"
            f"  🌙 夜间: {night_weather}, {night_temp}°C, {night_wind}风 {night_power}级"
        )

    return "\n".join(result)


@tool
def weather_query(city: str, extensions: Literal["base", "all"] = "base") -> str:
    """查询指定城市的天气信息

    可以查询当前天气或未来几天的天气预报。

    **重要提示：天气工具内部已经知道当前日期，不需要在调用前获取当前时间！**

    Args:
        city: 城市名称（如"北京"、"上海"、"杭州"）或城市编码
        extensions: 天气类型，"base"获取实时天气，"all"获取未来天气预报

    Returns:
        格式化的天气信息字符串
    """
    logger.info(f"🌤️ 调用天气查询工具: city={city}")
    return _get_weather_impl(city, extensions)


def get_weather_tools():
    return [weather_query]


WEATHER_TOOLS = get_weather_tools()
