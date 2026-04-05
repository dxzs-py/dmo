from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .views import (
    MyObtainTokenPairView,
    UserRegisterView,
    UserInfoView,
    CaptchaView,
    CaptchaVerifyView,
    SecureLogoutView
)

urlpatterns = [
    path('login/', MyObtainTokenPairView.as_view(), name='token_obtain_pair'),
    path('login/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('login/verify/', TokenVerifyView.as_view(), name='token_verify'),
    path('register/', UserRegisterView.as_view(), name='user_register'),
    path('info/', UserInfoView.as_view(), name='user_info'),
    path('captcha/', CaptchaView.as_view(), name='captcha'),
    path('captcha/verify/', CaptchaVerifyView.as_view(), name='captcha_verify'),
    path('secure-logout/', SecureLogoutView.as_view(), name='secure_logout'),
]
