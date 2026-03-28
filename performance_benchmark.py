"""
性能基准测试脚本
对比两个项目的性能表现
"""
import time
import requests
import statistics
from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class BenchmarkResult:
    """基准测试结果"""
    name: str
    mean_time: float
    median_time: float
    min_time: float
    max_time: float
    std_dev: float
    success_count: int
    total_count: int


class PerformanceBenchmark:
    """性能基准测试类"""
    
    def __init__(self, fastapi_url: str, django_url: str, num_iterations: int = 10):
        self.fastapi_url = fastapi_url
        self.django_url = django_url
        self.num_iterations = num_iterations
        
    def run_test(self, endpoint: str, method: str = "POST", data: Dict = None, 
                 name: str = "test") -> BenchmarkResult:
        """运行单个端点的测试"""
        times = []
        success_count = 0
        
        for i in range(self.num_iterations):
            try:
                start_time = time.time()
                
                if method == "POST":
                    response = requests.post(endpoint, json=data, timeout=60)
                else:
                    response = requests.get(endpoint, timeout=60)
                
                end_time = time.time()
                
                if response.status_code == 200:
                    success_count += 1
                    times.append(end_time - start_time)
                    print(f"  迭代 {i+1}/{self.num_iterations}: {(end_time - start_time)*1000:.2f}ms - 成功")
                else:
                    print(f"  迭代 {i+1}/{self.num_iterations}: HTTP {response.status_code} - 失败")
                    
            except Exception as e:
                print(f"  迭代 {i+1}/{self.num_iterations}: 错误 - {str(e)}")
        
        if not times:
            return BenchmarkResult(
                name=name,
                mean_time=0,
                median_time=0,
                min_time=0,
                max_time=0,
                std_dev=0,
                success_count=success_count,
                total_count=self.num_iterations
            )
        
        return BenchmarkResult(
            name=name,
            mean_time=statistics.mean(times),
            median_time=statistics.median(times),
            min_time=min(times),
            max_time=max(times),
            std_dev=statistics.stdev(times) if len(times) > 1 else 0,
            success_count=success_count,
            total_count=self.num_iterations
        )
    
    def compare_results(self, fastapi_result: BenchmarkResult, 
                       django_result: BenchmarkResult) -> Dict[str, Any]:
        """比较两个结果"""
        mean_diff = django_result.mean_time - fastapi_result.mean_time
        mean_diff_percent = (mean_diff / fastapi_result.mean_time * 100) if fastapi_result.mean_time > 0 else 0
        
        return {
            "fastapi": fastapi_result,
            "django": django_result,
            "mean_diff_ms": mean_diff * 1000,
            "mean_diff_percent": mean_diff_percent,
            "faster": "FastAPI" if mean_diff > 0 else "Django",
            "faster_percent": abs(mean_diff_percent)
        }
    
    def print_comparison(self, comparison: Dict[str, Any]):
        """打印比较结果"""
        print(f"\n=== {comparison['fastapi'].name} ===")
        print(f"FastAPI:")
        print(f"  平均: {comparison['fastapi'].mean_time*1000:.2f}ms")
        print(f"  中位数: {comparison['fastapi'].median_time*1000:.2f}ms")
        print(f"  最小: {comparison['fastapi'].min_time*1000:.2f}ms")
        print(f"  最大: {comparison['fastapi'].max_time*1000:.2f}ms")
        print(f"  成功率: {comparison['fastapi'].success_count}/{comparison['fastapi'].total_count}")
        
        print(f"\nDjango:")
        print(f"  平均: {comparison['django'].mean_time*1000:.2f}ms")
        print(f"  中位数: {comparison['django'].median_time*1000:.2f}ms")
        print(f"  最小: {comparison['django'].min_time*1000:.2f}ms")
        print(f"  最大: {comparison['django'].max_time*1000:.2f}ms")
        print(f"  成功率: {comparison['django'].success_count}/{comparison['django'].total_count}")
        
        print(f"\n比较结果:")
        print(f"  {comparison['faster']} 快 {comparison['faster_percent']:.2f}%")
        print(f"  平均差异: {comparison['mean_diff_ms']:.2f}ms")
    
    def run_chat_benchmark(self):
        """运行聊天端点基准测试"""
        print("\n=== 开始聊天端点基准测试 ===")
        
        chat_data = {
            "message": "你好，请介绍一下你自己",
            "mode": "default",
            "chat_history": []
        }
        
        print("\n测试 FastAPI 聊天端点...")
        fastapi_result = self.run_test(
            endpoint=f"{self.fastapi_url}/api/chat",
            method="POST",
            data=chat_data,
            name="Chat Endpoint"
        )
        
        print("\n测试 Django 聊天端点...")
        django_result = self.run_test(
            endpoint=f"{self.django_url}/api/chat/",
            method="POST",
            data=chat_data,
            name="Chat Endpoint"
        )
        
        comparison = self.compare_results(fastapi_result, django_result)
        self.print_comparison(comparison)
        return comparison
    
    def run_health_benchmark(self):
        """运行健康检查端点基准测试"""
        print("\n=== 开始健康检查端点基准测试 ===")
        
        print("\n测试 FastAPI 健康检查...")
        fastapi_result = self.run_test(
            endpoint=f"{self.fastapi_url}/health",
            method="GET",
            name="Health Check"
        )
        
        print("\n测试 Django 健康检查...")
        django_result = self.run_test(
            endpoint=f"{self.django_url}/api/health/",
            method="GET",
            name="Health Check"
        )
        
        comparison = self.compare_results(fastapi_result, django_result)
        self.print_comparison(comparison)
        return comparison
    
    def run_rag_query_benchmark(self):
        """运行RAG查询基准测试"""
        print("\n=== 开始RAG查询基准测试 ===")
        
        query_data = {
            "query": "什么是LangChain?",
            "index_name": "test_index"
        }
        
        print("\n测试 FastAPI RAG查询...")
        fastapi_result = self.run_test(
            endpoint=f"{self.fastapi_url}/api/rag/query",
            method="POST",
            data=query_data,
            name="RAG Query"
        )
        
        print("\n测试 Django RAG查询...")
        django_result = self.run_test(
            endpoint=f"{self.django_url}/api/rag/query/",
            method="POST",
            data=query_data,
            name="RAG Query"
        )
        
        comparison = self.compare_results(fastapi_result, django_result)
        self.print_comparison(comparison)
        return comparison
    
    def run_all_benchmarks(self):
        """运行所有基准测试"""
        print("=" * 60)
        print("开始性能基准测试")
        print("=" * 60)
        
        results = {
            "health": self.run_health_benchmark(),
            "chat": self.run_chat_benchmark(),
            "rag_query": self.run_rag_query_benchmark()
        }
        
        print("\n" + "=" * 60)
        print("基准测试完成")
        print("=" * 60)
        
        return results


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="性能基准测试工具")
    parser.add_argument("--fastapi-url", default="http://localhost:8000", 
                       help="FastAPI服务URL")
    parser.add_argument("--django-url", default="http://localhost:8001", 
                       help="Django服务URL")
    parser.add_argument("--iterations", type=int, default=10,
                       help="每个端点的测试迭代次数")
    
    args = parser.parse_args()
    
    benchmark = PerformanceBenchmark(
        fastapi_url=args.fastapi_url,
        django_url=args.django_url,
        num_iterations=args.iterations
    )
    
    results = benchmark.run_all_benchmarks()
    
    return results


if __name__ == "__main__":
    main()
