"""CacheControlMiddleware 路径策略测试。"""

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from Django_xm.apps.core.middleware.cache import CacheControlMiddleware


class CacheControlMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _run(self, path):
        request = self.factory.get(path)
        middleware = CacheControlMiddleware(lambda req: HttpResponse())
        return middleware(request)

    def test_chat_no_store(self):
        response = self._run("/api/v1/chat/sessions/")
        self.assertEqual(
            response["Cache-Control"],
            "no-store, no-cache, must-revalidate",
        )

    def test_research_no_store(self):
        response = self._run("/api/v1/research/tasks/")
        self.assertEqual(
            response["Cache-Control"],
            "no-store, no-cache, must-revalidate",
        )

    def test_knowledge_short_cache(self):
        response = self._run("/api/v1/knowledge/documents/")
        self.assertEqual(response["Cache-Control"], "private, max-age=300")

    def test_cache_manager_no_store(self):
        response = self._run("/api/v1/cache/keys/")
        self.assertEqual(
            response["Cache-Control"],
            "no-store, no-cache, must-revalidate",
        )

    def test_default_policy_for_other_api(self):
        response = self._run("/api/v1/tools/list/")
        self.assertEqual(response["Cache-Control"], "private, max-age=60")

    def test_non_api_path_untouched(self):
        response = self._run("/admin/")
        self.assertNotIn("Cache-Control", response)

    def test_existing_cache_control_preserved(self):
        request = self.factory.get("/api/v1/chat/sessions/")

        def get_response(req):
            response = HttpResponse()
            response["Cache-Control"] = "max-age=0"
            return response

        response = CacheControlMiddleware(get_response)(request)
        self.assertEqual(response["Cache-Control"], "max-age=0")
        self.assertNotIn("X-Cache-Policy", response)
