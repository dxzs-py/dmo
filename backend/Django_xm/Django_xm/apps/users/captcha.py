import io
import random
from PIL import Image, ImageDraw, ImageFont
from django.core.cache import cache


class CaptchaGenerator:
    """
    图形验证码生成器
    """
    
    def __init__(self, width=120, height=40, length=4, font_size=28):
        self.width = width
        self.height = height
        self.length = length
        self.font_size = font_size
        self.chars = 'abcdefghijkmnpqrstuvwxyz23456789'
    
    def generate_code(self):
        """
        生成随机验证码
        """
        return ''.join(random.choice(self.chars) for _ in range(self.length))
    
    def generate_image(self, code):
        """
        生成验证码图片
        """
        img = Image.new('RGB', (self.width, self.height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype('arial.ttf', self.font_size)
        except IOError:
            font = ImageFont.load_default()
        
        for i, char in enumerate(code):
            x = 10 + i * 25
            y = random.randint(2, 8)
            color = (random.randint(0, 100), random.randint(0, 100), random.randint(0, 100))
            draw.text((x, y), char, font=font, fill=color)
        
        for _ in range(random.randint(3, 6)):
            x1 = random.randint(0, self.width)
            y1 = random.randint(0, self.height)
            x2 = random.randint(0, self.width)
            y2 = random.randint(0, self.height)
            color = (random.randint(100, 200), random.randint(100, 200), random.randint(100, 200))
            draw.line([(x1, y1), (x2, y2)], fill=color, width=1)
        
        for _ in range(random.randint(50, 100)):
            x = random.randint(0, self.width)
            y = random.randint(0, self.height)
            color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            draw.point((x, y), fill=color)
        
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf
    
    def generate(self, key=None):
        """
        生成验证码并存储到缓存
        """
        code = self.generate_code().lower()
        image = self.generate_image(code)
        
        if key:
            cache.set(f'captcha:{key}', code, timeout=300)
        
        return code, image
