from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .views import (
    MyObtainTokenPairView,
    UserRegisterView,
    UserInfoView,
    CaptchaView,
    CaptchaVerifyView,
    SecureLogoutView,
    UserProfileView,
    UserAvatarView,
    ChangePasswordView,
    BindPhoneView,
    UserPreferencesView,
    UserUsageStatsView,
    UserAccountDeleteView,
)

urlpatterns = [
    path('login/', MyObtainTokenPairView.as_view(), name='token_obtain_pair'),
    path('login/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('login/verify/', TokenVerifyView.as_view(), name='token_verify'),
    path('register/', UserRegisterView.as_view(), name='user_register'),
    path('info/', UserInfoView.as_view(), name='user_info'),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('avatar/', UserAvatarView.as_view(), name='user_avatar'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('bind-phone/', BindPhoneView.as_view(), name='bind_phone'),
    path('captcha/', CaptchaView.as_view(), name='captcha'),
    path('captcha/verify/', CaptchaVerifyView.as_view(), name='captcha_verify'),
    path('secure-logout/', SecureLogoutView.as_view(), name='secure_logout'),
    path('preferences/', UserPreferencesView.as_view(), name='user_preferences'),
    path('usage-stats/', UserUsageStatsView.as_view(), name='user_usage_stats'),
    path('account/', UserAccountDeleteView.as_view(), name='user_account_delete'),
]
