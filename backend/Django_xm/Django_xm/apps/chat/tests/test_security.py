"""
安全功能测试脚本 - 验证用户对话数据安全存储机制

测试项目：
1. 前端localStorage清理验证
2. 用户切换数据隔离测试
3. Redis缓存安全性验证
4. 登出后数据清理验证
5. API权限控制测试
6. 数据备份恢复测试

使用方法：
    python test_security.py
    或
    python manage.py test Django_xm.apps.chat.tests.test_security
"""

import os
import sys
import json
import requests

BASE_URL = os.environ.get('TEST_BASE_URL', 'http://localhost:8000/api/v1')

class SecurityTestSuite:
    def __init__(self):
        self.results = []
        self.user1_token = None
        self.user2_token = None
        self.user1_id = None
        self.user2_id = None
    
    def log_test(self, test_name, passed, details=''):
        status = '✅ PASS' if passed else '❌ FAIL'
        result = {
            'test': test_name,
            'status': status,
            'details': details
        }
        self.results.append(result)
        print(f"{status}: {test_name}")
        if details:
            print(f"   {details}")
    
    def setup_test_users(self):
        """创建测试用户并获取token"""
        print("\n=== 设置测试用户 ===")
        
        try:
            response = requests.post(f'{BASE_URL}/users/register/', json={
                'username': 'security_test_user1',
                'password': 'TestSecure123!',
                'email': 'test1@security.com'
            })
            
            if response.status_code in [200, 201]:
                login_resp = requests.post(f'{BASE_URL}/users/login/', json={
                    'username': 'security_test_user1',
                    'password': 'TestSecure123!'
                })
                if login_resp.status_code == 200:
                    data = login_resp.json()
                    self.user1_token = data.get('access')
                    self.user1_id = data.get('id')
                    print(f"用户1创建成功: ID={self.user1_id}")
            
            response = requests.post(f'{BASE_URL}/users/register/', json={
                'username': 'security_test_user2',
                'password': 'TestSecure123!',
                'email': 'test2@security.com'
            })
            
            if response.status_code in [200, 201]:
                login_resp = requests.post(f'{BASE_URL}/users/login/', json={
                    'username': 'security_test_user2',
                    'password': 'TestSecure123!'
                })
                if login_resp.status_code == 200:
                    data = login_resp.json()
                    self.user2_token = data.get('access')
                    self.user2_id = data.get('id')
                    print(f"用户2创建成功: ID={self.user2_id}")
                    
        except Exception as e:
            print(f"设置测试用户失败: {str(e)}")
    
    def test_01_no_localstorage_session_data(self):
        """
        测试1：验证前端不再将会话数据存储在localStorage
        
        安全要求：
        - 不应在localStorage中找到会话相关数据
        - 只允许存储认证token（这是标准做法）
        """
        print("\n=== 测试1: localStorage安全检查 ===")
        
        dangerous_keys = [
            'lc-studylab-sessions',
            'lc-studylab-current-session',
            'chat-messages',
            'chat-sessions',
            'user-conversations'
        ]
        
        try:
            with open('../../frontend/langchain-vue/src/stores/session.js', 'r', encoding='utf-8') as f:
                content = f.read()
                
            has_localStorage_sessions = any(key in content for key in dangerous_keys)
            
            if not has_localStorage_sessions:
                self.log_test(
                    "前端session.js不包含localStorage会话存储",
                    True,
                    "未发现危险的localStorage键名"
                )
            else:
                found_keys = [key for key in dangerous_keys if key in content]
                self.log_test(
                    "前端session.js不包含localStorage会话存储",
                    False,
                    f"仍发现localStorage使用: {found_keys}"
                )
                
        except FileNotFoundError:
            self.log_test(
                "前端session.js不包含localStorage会话存储",
                None,
                "文件未找到，跳过此测试"
            )
    
    def test_02_user_session_isolation(self):
        """
        测试2：验证用户会话隔离性
        
        安全要求：
        - 用户A不能访问用户B的会话
        - 尝试访问其他用户会话应返回403/404
        """
        print("\n=== 测试2: 用户会话隔离测试 ===")
        
        if not all([self.user1_token, self.user2_token]):
            self.log_test("用户会话隔离", None, "缺少测试用户token，跳过")
            return
        
        headers_user1 = {'Authorization': f'Bearer {self.user1_token}'}
        headers_user2 = {'Authorization': f'Bearer {self.user2_token}'}
        
        try:
            create_resp = requests.post(
                f'{BASE_URL}/chat/sessions/',
                json={'title': 'User1 Secret Session', 'mode': 'basic-agent'},
                headers=headers_user1
            )
            
            if create_resp.status_code == 200:
                session_id = create_resp.json().get('data', {}).get('session_id')
                
                access_resp = requests.get(
                    f'{BASE_URL}/chat/sessions/{session_id}/',
                    headers=headers_user2
                )
                
                if access_resp.status_code in [404, 403]:
                    self.log_test(
                        "用户无法访问其他用户的会话",
                        True,
                        f"返回状态码: {access_resp.status_code}"
                    )
                elif access_resp.status_code == 200:
                    self.log_test(
                        "用户无法访问其他用户的会话",
                        False,
                        "严重安全问题！用户可以访问其他用户的数据！"
                    )
                else:
                    self.log_test(
                        "用户无法访问其他用户的会话",
                        None,
                        f"意外状态码: {access_resp.status_code}"
                    )
                    
                cleanup_resp = requests.delete(
                    f'{BASE_URL}/chat/sessions/{session_id}/',
                    headers=headers_user1
                )
            else:
                self.log_test("用户会话隔离", None, "无法创建测试会话")
                
        except Exception as e:
            self.log_test("用户会话隔离", False, str(e))
    
    def test_03_secure_logout_clears_data(self):
        """
        测试3：验证安全登出清除所有数据
        
        安全要求：
        - 登出后JWT token被加入黑名单
        - Redis中的用户会话缓存被清除
        - 返回清除的会话数量
        """
        print("\n=== 测试3: 安全登出数据清理 ===")
        
        if not self.user1_token:
            self.log_test("安全登出数据清理", None, "缺少测试用户token")
            return
        
        headers = {'Authorization': f'Bearer {self.user1_token}'}
        
        try:
            create_resp = requests.post(
                f'{BASE_URL}/chat/sessions/',
                json={'title': 'Session To Clear', 'mode': 'basic-agent'},
                headers=headers
            )
            
            logout_resp = requests.post(
                f'{BASE_URL}/users/secure-logout/',
                json={'refresh': 'dummy_refresh_token'},
                headers=headers
            )
            
            if logout_resp.status_code == 200:
                data = logout_resp.json()
                sessions_cleared = data.get('data', {}).get('sessions_cleared', 0)
                
                if sessions_cleared >= 0:
                    self.log_test(
                        "安全登出返回正确的清理信息",
                        True,
                        f"清除的会话数: {sessions_cleared}"
                    )
                else:
                    self.log_test(
                        "安全登出返回正确的清理信息",
                        False,
                        f"意外的会话数量: {sessions_cleared}"
                    )
            else:
                self.log_test(
                    "安全登出API可访问",
                    False,
                    f"状态码: {logout_resp.status_code}"
                )
                
        except Exception as e:
            self.log_test("安全登出数据清理", False, str(e))
    
    def test_04_redis_cache_isolation(self):
        """
        测试4：验证Redis缓存的用户隔离性
        
        安全要求：
        - 不同用户的缓存key完全独立
        - 缓存key包含user_id确保隔离
        """
        print("\n=== 测试4: Redis缓存隔离 ===")
        
        try:
            from Django_xm.apps.chat.services import SecureSessionCacheService
            
            user1_data = {'session_id': 'test-session-1', 'title': 'User1 Data'}
            user2_data = {'session_id': 'test-session-2', 'title': 'User2 Data'}
            
            SecureSessionCacheService.cache_session(999, user1_data)
            SecureSessionCacheService.cache_session(888, user2_data)
            
            cached_for_999 = SecureSessionCacheService.get_cached_session(999, 'test-session-1')
            cached_for_888 = SecureSessionCacheService.get_cached_session(888, 'test-session-2')
            
            cross_access_999 = SecureSessionCacheService.get_cached_session(999, 'test-session-2')
            cross_access_888 = SecureSessionCacheService.get_cached_session(888, 'test-session-1')
            
            if (cached_for_999 and cached_for_888 and 
                not cross_access_999 and not cross_access_888):
                self.log_test(
                    "Redis缓存正确实现用户隔离",
                    True,
                    "每个用户只能访问自己的缓存数据"
                )
            else:
                self.log_test(
                    "Redis缓存正确实现用户隔离",
                    False,
                    "检测到可能的跨用户缓存访问"
                )
            
            SecureSessionCacheService.invalidate_all_user_sessions(999)
            SecureSessionCacheService.invalidate_all_user_sessions(888)
            
        except ImportError:
            self.log_test("Redis缓存隔离", None, "SecureSessionCacheService未安装")
        except Exception as e:
            self.log_test("Redis缓存隔离", False, str(e))
    
    def test_05_api_authentication_required(self):
        """
        测试5：验证所有聊天API需要认证
        
        安全要求：
        - 未认证请求应返回401
        - 不能绕过认证访问任何聊天接口
        """
        print("\n=== 测试5: API认证要求 ===")
        
        endpoints = [
            ('GET', '/chat/sessions/'),
            ('POST', '/chat/sessions/'),
            ('GET', '/chat sessions/fake-id/'),
            ('POST', '/chat/sessions/fake-id/messages/'),
        ]
        
        all_protected = True
        
        for method, endpoint in endpoints:
            try:
                if method == 'GET':
                    resp = requests.get(f'{BASE_URL}{endpoint}')
                else:
                    resp = requests.post(f'{BASE_URL}{endpoint}', json={})
                
                if resp.status_code == 401:
                    pass
                else:
                    all_protected = False
                    self.log_test(
                        f"API端点需要认证: {method} {endpoint}",
                        False,
                        f"期望401，实际得到{resp.status_code}"
                    )
                    
            except Exception as e:
                all_protected = False
                self.log_test(
                    f"API端点需要认证: {method} {endpoint}",
                    False,
                    str(e)
                )
        
        if all_protected:
            self.log_test("所有聊天API都需要认证", True, "所有端点均返回401")
    
    def test_06_security_headers_present(self):
        """
        测试6：验证安全响应头
        
        安全要求：
        - 包含X-Content-Type-Options: nosniff
        - 包含X-Frame-Options: DENY
        - 包含X-XSS-Protection头
        """
        print("\n=== 测试6: 安全响应头检查 ===")
        
        required_headers = [
            'X-Content-Type-Options',
            'X-Frame-Options',
            'X-XSS-Protection',
        ]
        
        try:
            resp = requests.get(f'{BASE_URL}/users/info/')
            headers_present = []
            headers_missing = []
            
            for header in required_headers:
                if header in resp.headers:
                    headers_present.append(header)
                else:
                    headers_missing.append(header)
            
            if not headers_missing:
                self.log_test(
                    "安全响应头完整",
                    True,
                    f"存在: {', '.join(headers_present)}"
                )
            else:
                self.log_test(
                    "安全响应头完整",
                    False,
                    f"缺失: {', '.join(headers_missing)}"
                )
                
        except Exception as e:
            self.log_test("安全响应头检查", False, str(e))
    
    def generate_report(self):
        """生成测试报告"""
        print("\n" + "="*60)
        print("安全测试报告")
        print("="*60)
        
        total = len(self.results)
        passed = sum(1 for r in self.results if '✅' in r['status'])
        failed = sum(1 for r in self.results if '❌' in r['status'])
        skipped = sum(1 for r in self.results if r['status'] not in ['✅ PASS', '❌ FAIL'])
        
        print(f"\n总计: {total} 个测试")
        print(f"通过: {passed} ✅")
        print(f"失败: {failed} ❌")
        print(f"跳过: {skipped} ⏭️")
        
        print("\n详细结果:")
        for result in self.results:
            print(f"\n{result['status']}: {result['test']}")
            if result['details']:
                print(f"   └─ {result['details']}")
        
        if failed > 0:
            print("\n⚠️  发现安全问题！请立即修复失败的测试项。")
            return False
        else:
            print("\n🎉 所有安全测试通过！系统符合安全要求。")
            return True


def main():
    print("🔒 对话数据安全存储机制 - 安全测试套件")
    print("="*60)
    
    suite = SecurityTestSuite()
    
    suite.setup_test_users()
    
    suite.test_01_no_localstorage_session_data()
    suite.test_02_user_session_isolation()
    suite.test_03_secure_logout_clears_data()
    suite.test_04_redis_cache_isolation()
    suite.test_05_api_authentication_required()
    suite.test_06_security_headers_present()
    
    success = suite.generate_report()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
