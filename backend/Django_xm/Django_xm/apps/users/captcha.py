"""
图形验证码模块（生成 + 校验）

本模块是验证码功能的**唯一权威实现**，把"出题"（生成）与"阅卷"（校验）放在
同一文件，原因：
    - 二者通过缓存键 ``captcha:<key>`` 隐式耦合，拆到不同包容易被单侧改动
      破坏成"永远校验失败"的可用性故障；
    - 校验侧（CaptchaMixin）零 DRF 依赖，合并不会把视图层依赖带进工具模块。

消费者：apps/users/views.py 的登录（MyObtainTokenPairView）与
注册（UserRegisterView）两个敏感接口。

字体：使用仓库内置的 DejaVu Sans（apps/users/fonts/，Bitstream Vera 许可），
路径由 settings.CAPTCHA_FONT_PATH 配置。不依赖系统字体，因为 Linux / 容器镜像
默认不提供 arial.ttf；且字体缺失时若降级为ImageFont.load_default() 而不传 size，
font_size 会被丢弃、字符仅 6px 高导致验证码不可辨认（故降级必须传 size）。

排布：画布尺寸与字符步进均由 font_size / length 推导，不使用硬编码常量，
保证任意字号 / 长度下字符都不溢出画布。

依赖层级：本模块只依赖 common/（app 无关底层），不依赖任何业务 app。
"""

import hmac
import io
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from PIL import Image, ImageDraw, ImageFont

from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError

logger = logging.getLogger(__name__)

# --- 缓存契约（生产侧与消费侧共用，防止键前缀漂移）---

#: 验证码答案在缓存中的键前缀，生成侧与校验侧必须一致
CAPTCHA_CACHE_PREFIX = "captcha:"
#: 验证码有效期（秒）。超时后校验侧报"验证码已过期"
CAPTCHA_TTL_SECONDS = 300


def _cache_key(key):
    """拼接验证码缓存键，统一键格式的唯一出口。"""
    return f"{CAPTCHA_CACHE_PREFIX}{key}"


def _secrets_randint(a, b):
    """
    返回 [a, b] 区间内的密码学安全随机整数。

    替代 random.randint，用于验证码图像的噪声坐标/颜色生成，
    保证整张验证码图像的随机性均不可预测，增强抗 OCR 能力。
    """
    return a + secrets.randbelow(b - a + 1)


class CaptchaGenerator:
    """
    图形验证码生成器
    """

    def __init__(self, width=None, height=None, length=4, font_size=28):
        """
        Args:
            width / height: 画布尺寸。None 时按 font_size / length 自动推导
                （此前固定 120x40，调大字号或加长验证码会撑破画布）。
            length: 验证码字符数。
            font_size: 字号（像素）。
        """
        self.length = length
        self.font_size = font_size
        # 剔除易混淆字符（0/O、1/l/I），降低人类误读率
        self.chars = "abcdefghijkmnpqrstuvwxyz23456789"

        # 排布参数全部由 font_size / length 推导，不使用硬编码常量，
        # 保证任意字号 / 长度下字符都不会溢出画布。
        self.cell_width = max(10, int(font_size * 0.9))  # 单字符占位宽
        self.padding_x = max(8, int(font_size * 0.35))  # 左右留白
        self.width = width if width else self.padding_x * 2 + self.cell_width * length
        self.height = height if height else max(32, int(font_size * 1.45))

    def generate_code(self):
        """
        生成随机验证码（使用 secrets 模块确保不可预测性）
        """
        return "".join(secrets.choice(self.chars) for _ in range(self.length))

    def _load_font(self):
        """按 ``settings.CAPTCHA_FONT_PATH`` 加载字体。

        降级时必须传入 size：``ImageFont.load_default()`` 不带 size 会丢弃
        font_size，渲染出的字符仅 6px 高（画布 40px），验证码将无法辨认。
        """
        font_path = getattr(settings, "CAPTCHA_FONT_PATH", None)
        if font_path:
            try:
                return ImageFont.truetype(str(font_path), self.font_size)
            except OSError:
                logger.warning(
                    "[Captcha] 字体加载失败，降级为内置字体: %s", font_path, exc_info=True
                )
        return ImageFont.load_default(size=self.font_size)

    @staticmethod
    def _text_height(draw, font):
        """量取当前字体在给定字号下的实际渲染高度（像素），用于垂直居中与防溢出。"""
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return max(1, bbox[3] - bbox[1])

    def generate_image(self, code):
        """
        生成验证码图片
        """
        img = Image.new("RGB", (self.width, self.height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        font = self._load_font()

        # 垂直排布：按字体实际渲染高度居中，再叠加随机抖动（避免字符贴边）
        text_h = self._text_height(draw, font)
        base_y = max(0, (self.height - text_h) // 2)
        jitter_y = max(1, self.height - text_h - base_y)

        for i, char in enumerate(code):
            x = self.padding_x + i * self.cell_width
            y = base_y + _secrets_randint(0, jitter_y)
            color = (_secrets_randint(0, 100), _secrets_randint(0, 100), _secrets_randint(0, 100))
            draw.text((x, y), char, font=font, fill=color)

        for _ in range(_secrets_randint(3, 6)):
            x1 = _secrets_randint(0, self.width)
            y1 = _secrets_randint(0, self.height)
            x2 = _secrets_randint(0, self.width)
            y2 = _secrets_randint(0, self.height)
            color = (_secrets_randint(100, 200), _secrets_randint(100, 200), _secrets_randint(100, 200))
            draw.line([(x1, y1), (x2, y2)], fill=color, width=1)

        for _ in range(_secrets_randint(50, 100)):
            x = _secrets_randint(0, self.width)
            y = _secrets_randint(0, self.height)
            color = (_secrets_randint(0, 255), _secrets_randint(0, 255), _secrets_randint(0, 255))
            draw.point((x, y), fill=color)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        # 必须复位读指针，否则 HttpResponse 从末尾读起得到空响应体
        buf.seek(0)
        return buf

    def generate(self, key=None):
        """
        生成验证码并存储到缓存

        Args:
            key: 验证码唯一标识（由调用方生成的 uuid）。为 None 时不写缓存，
                 此时返回的 code 无法被校验，仅供测试/调试使用。

        Returns:
            tuple[str, io.BytesIO]: (小写答案, PNG 图片字节流)。
            注意：答案已写入缓存，调用方仅需使用图片；返回 code 是为了
            测试与排障，生产链路请勿拿它当答案比对（应以缓存为准）。
        """
        code = self.generate_code().lower()
        image = self.generate_image(code)

        if key:
            cache.set(_cache_key(key), code, timeout=CAPTCHA_TTL_SECONDS)

        return code, image


class CaptchaMixin:
    """
    验证码校验 Mixin

    在视图的 post() 方法中调用 verify_captcha() 进行验证码校验。
    校验成功或未提供验证码（验证码为可选）时静默返回；
    校验失败抛出 BaseAppError，冒泡至全局异常处理器。
    """

    @staticmethod
    def verify_captcha(request_data):
        """
        校验验证码

        实现为静态方法：校验只依赖缓存与请求数据，不读取任何实例状态；
        调用侧仍写作 ``self.verify_captcha(request.data)``，MRO 可正常解析。

        Args:
            request_data: 请求数据（dict 或 QueryDict）

        Raises:
            BaseAppError: 验证码已过期或错误（code=CAPTCHA_ERROR，HTTP 400）
        """
        captcha_key = request_data.get("captcha_key")
        captcha_code = request_data.get("captcha", "").lower()

        if not captcha_key or not captcha_code:
            return

        stored_code = cache.get(_cache_key(captcha_key))

        if not stored_code:
            raise BaseAppError("验证码已过期，请刷新", business_code=ErrorCode.CAPTCHA_ERROR)

        # 常量时间比较，防时序攻击
        if not hmac.compare_digest(str(stored_code), str(captcha_code)):
            raise BaseAppError("验证码错误", business_code=ErrorCode.CAPTCHA_ERROR)

        # 一次性验证码：比对成功后立即销毁
        cache.delete(_cache_key(captcha_key))
