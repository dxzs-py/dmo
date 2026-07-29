import io
import secrets

from django.core.cache import cache
from PIL import Image, ImageDraw, ImageFont


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

    def __init__(self, width=120, height=40, length=4, font_size=28):
        self.width = width
        self.height = height
        self.length = length
        self.font_size = font_size
        self.chars = "abcdefghijkmnpqrstuvwxyz23456789"

    def generate_code(self):
        """
        生成随机验证码（使用 secrets 模块确保不可预测性）
        """
        return "".join(secrets.choice(self.chars) for _ in range(self.length))

    def generate_image(self, code):
        """
        生成验证码图片
        """
        img = Image.new("RGB", (self.width, self.height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("arial.ttf", self.font_size)
        except OSError:
            font = ImageFont.load_default()

        for i, char in enumerate(code):
            x = 10 + i * 25
            y = _secrets_randint(2, 8)
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
        buf.seek(0)
        return buf

    def generate(self, key=None):
        """
        生成验证码并存储到缓存
        """
        code = self.generate_code().lower()
        image = self.generate_image(code)

        if key:
            cache.set(f"captcha:{key}", code, timeout=300)

        return code, image
