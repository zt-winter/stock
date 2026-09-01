#!/usr/bin/env python3
"""
财务报告PDF验证脚本
功能：独立验证PDF文件是否为指定公司的正式财务报告
可用于验证已下载文件或批量验证目录中的文件

PDF后端支持（按优先级）：PyMuPDF > pypdf > pdfminer.six
"""

import os
import sys
import argparse
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import glob

# 导入兼容层：优先从 scripts.pdf_helper 导入，支持独立运行和模块导入两种方式
try:
    from scripts.pdf_helper import open_pdf, get_pdf_backend
except ImportError:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)
    from pdf_helper import open_pdf, get_pdf_backend

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PDFValidator:
    """PDF验证器类"""
    
    def __init__(self):
        """初始化验证器"""
        # 报告类型关键词定义
        self.report_keywords = {
            "annual": {
                "zh": ["年报", "年度报告", "全年业绩", "年度业绩"],
                "zh_Hant": ["年報", "全年業績", "年度業績"],
                "en": ["Annual Report", "Annual", "Annual Results"]
            },
            "interim": {
                "zh": ["中报", "中期报告", "半年度报告", "中期业绩", "半年度业绩"],
                "zh_Hant": ["中期報告", "半年度報告", "中期業績", "半年度業績"],
                "en": ["Interim Report", "Interim", "Half-year Report", "Interim Results"]
            }
        }
        
        # 排除关键词
        self.exclusion_keywords = {
            "zh": ["补充", "补充公告", "补充报告", "业绩预告", "盈利预告", "演示稿", "新闻稿"],
            "en": ["Supplement", "Supplementary", "Addendum", "Profit Alert", "Trading Update", "Presentation", "Press Release"]
        }
        
        logger.info(f"PDF验证器初始化完成，使用后端: {get_pdf_backend()}")
    
    def validate_file(self, pdf_path: str, company_name: Optional[str] = None, 
                     report_type: Optional[str] = None) -> Dict:
        """
        验证单个PDF文件
        
        Args:
            pdf_path: PDF文件路径
            company_name: 公司名称（用于验证匹配）
            report_type: 报告类型（可选，"annual"或"interim"）
            
        Returns:
            验证结果字典
        """
        result = {
            "file_path": pdf_path,
            "file_name": os.path.basename(pdf_path),
            "file_size": 0,
            "validation_time": datetime.now().isoformat(),
            "company_match": None if company_name is None else False,
            "report_type_match": None if report_type is None else False,
            "exclusion_match": False,
            "keywords_found": [],
            "first_page_text": "",
            "page_count": 0,
            "validation_result": "UNKNOWN",
            "confidence_score": 0.0,
            "validation_details": "",
            "quality_metrics": {}
        }
        
        try:
            # 检查文件是否存在
            if not os.path.exists(pdf_path):
                result["validation_result"] = "ERROR"
                result["validation_details"] = "文件不存在"
                return result
            
            # 获取文件大小
            result["file_size"] = os.path.getsize(pdf_path)
            
            # 打开PDF文件并提取信息
            with open_pdf(pdf_path) as pdf:
                result["page_count"] = len(pdf.pages)
                
                if len(pdf.pages) == 0:
                    result["validation_result"] = "ERROR"
                    result["validation_details"] = "PDF文件无页面"
                    return result
                
                # 提取第一页文本
                first_page = pdf.pages[0]
                text = first_page.extract_text()
                
                if not text:
                    result["validation_result"] = "WARNING"
                    result["validation_details"] = "无法提取文本（可能是扫描版PDF）"
                    return result
                
                # 保存第一页文本（前500字符）
                result["first_page_text"] = text[:500] + "..." if len(text) > 500 else text
                text_lower = text.lower()
                
                # 生成质量指标
                result["quality_metrics"] = self._calculate_quality_metrics(pdf)
                
                # 验证公司名称（如果提供了）
                if company_name:
                    company_keywords = self._generate_company_keywords(company_name)
                    company_found = False
                    
                    for keyword in company_keywords:
                        if keyword.lower() in text_lower:
                            company_found = True
                            result["keywords_found"].append(f"公司:{keyword}")
                            break
                    
                    result["company_match"] = company_found
                
                # 验证报告类型（如果提供了）
                if report_type:
                    report_type_found = False
                    
                    for lang in ["zh", "zh_Hant", "en"]:
                        if lang in self.report_keywords[report_type]:
                            for keyword in self.report_keywords[report_type][lang]:
                                if keyword.lower() in text_lower:
                                    report_type_found = True
                                    result["keywords_found"].append(f"报告:{keyword}")
                                    break
                        if report_type_found:
                            break
                    
                    result["report_type_match"] = report_type_found
                
                # 检查排除关键词
                exclusion_found = False
                for lang in ["zh", "en"]:
                    if lang in self.exclusion_keywords:
                        for keyword in self.exclusion_keywords[lang]:
                            if keyword.lower() in text_lower:
                                exclusion_found = True
                                result["keywords_found"].append(f"排除:{keyword}")
                                break
                    if exclusion_found:
                        break
                
                result["exclusion_match"] = exclusion_found
                
                # 计算置信度分数
                confidence = 0.0
                
                if company_name and result["company_match"]:
                    confidence += 0.4
                elif not company_name:
                    confidence += 0.2  # 未验证公司名称的基础分
                
                if report_type and result["report_type_match"]:
                    confidence += 0.4
                elif not report_type:
                    confidence += 0.2  # 未验证报告类型的基础分
                
                if not result["exclusion_match"]:
                    confidence += 0.2
                
                # 质量指标加分
                if result["quality_metrics"].get("text_ratio", 0) > 0.5:
                    confidence += 0.1
                if result["page_count"] >= 20:
                    confidence += 0.1
                
                result["confidence_score"] = min(confidence, 1.0)  # 限制在0-1之间
                
                # 确定验证结果
                if exclusion_found:
                    result["validation_result"] = "FAIL"
                    result["validation_details"] = "包含排除关键词"
                elif company_name and not result["company_match"]:
                    result["validation_result"] = "FAIL"
                    result["validation_details"] = "公司名称不匹配"
                elif report_type and not result["report_type_match"]:
                    result["validation_result"] = "FAIL"
                    result["validation_details"] = "报告类型不匹配"
                elif (company_name and result["company_match"]) and (report_type and result["report_type_match"]):
                    result["validation_result"] = "PASS"
                    result["validation_details"] = "验证通过"
                else:
                    result["validation_result"] = "PARTIAL"
                    if company_name and report_type:
                        result["validation_details"] = "部分匹配，需要人工检查"
                    else:
                        result["validation_details"] = "验证条件不完整"
                
                logger.info(f"文件验证完成: {pdf_path} -> {result['validation_result']}")
                return result
                
        except Exception as e:
            result["validation_result"] = "ERROR"
            result["validation_details"] = f"验证异常: {str(e)}"
            logger.error(f"验证文件异常: {pdf_path}, 错误: {str(e)}")
            return result
    
    def validate_directory(self, directory: str, recursive: bool = False, 
                          report_type: Optional[str] = None) -> Dict:
        """
        验证目录中的所有PDF文件
        
        Args:
            directory: 目录路径
            recursive: 是否递归搜索子目录
            report_type: 报告类型（可选）
            
        Returns:
            批量验证结果
        """
        results = {
            "directory": directory,
            "validation_time": datetime.now().isoformat(),
            "total_files": 0,
            "passed": 0,
            "failed": 0,
            "partial": 0,
            "errors": 0,
            "files": []
        }
        
        try:
            # 搜索PDF文件
            pattern = os.path.join(directory, "**/*.pdf") if recursive else os.path.join(directory, "*.pdf")
            pdf_files = glob.glob(pattern, recursive=recursive)
            
            results["total_files"] = len(pdf_files)
            logger.info(f"找到 {len(pdf_files)} 个PDF文件在目录 {directory}")
            
            # 验证每个文件
            for pdf_path in pdf_files:
                # 从文件名解析公司名称（如果可能）
                company_name = self._parse_company_from_filename(os.path.basename(pdf_path))
                
                # 验证文件
                file_result = self.validate_file(pdf_path, company_name, report_type)
                
                # 更新统计
                if file_result["validation_result"] == "PASS":
                    results["passed"] += 1
                elif file_result["validation_result"] == "FAIL":
                    results["failed"] += 1
                elif file_result["validation_result"] == "PARTIAL":
                    results["partial"] += 1
                else:
                    results["errors"] += 1
                
                results["files"].append(file_result)
            
            logger.info(f"目录验证完成: {directory}, 通过: {results['passed']}, 失败: {results['failed']}")
            return results
            
        except Exception as e:
            logger.error(f"验证目录异常: {directory}, 错误: {str(e)}")
            results["error"] = str(e)
            return results
    
    def _generate_company_keywords(self, company_name: str) -> List[str]:
        """
        生成公司名称关键词列表
        
        Args:
            company_name: 公司名称
            
        Returns:
            关键词列表
        """
        keywords = [company_name]
        
        # 移除空格
        keywords.append(company_name.replace(" ", ""))
        
        # 添加公司后缀变体
        if not company_name.endswith("公司"):
            keywords.append(company_name + "有限公司")
            keywords.append(company_name + "股份有限公司")
        
        # 港股公司：添加繁体版本
        if self._contains_chinese(company_name):
            # 这里可以添加简体转繁体的逻辑
            # 暂时使用相同名称
            keywords.append(company_name)
        
        # 英文处理：全大写
        if any(c.isalpha() for c in company_name) and not self._contains_chinese(company_name):
            keywords.append(company_name.upper())
            keywords.append(company_name.replace(".", "").replace(",", ""))
        
        return list(set(keywords))  # 去重
    
    def _contains_chinese(self, text: str) -> bool:
        """检查文本是否包含中文"""
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False
    
    def _calculate_quality_metrics(self, pdf) -> Dict:
        """
        计算PDF质量指标
        
        Args:
            pdf: open_pdf() 返回的 PDFWrapper 对象
            
        Returns:
            质量指标字典
        """
        metrics = {
            "page_count": len(pdf.pages),
            "total_text_length": 0,
            "text_ratio": 0.0,
            "estimated_pages_with_text": 0
        }
        
        try:
            # 计算总文本长度
            total_text = ""
            pages_with_text = 0
            
            for page in pdf.pages:
                text = page.extract_text()
                if text and len(text.strip()) > 0:
                    total_text += text
                    pages_with_text += 1
            
            metrics["total_text_length"] = len(total_text)
            metrics["estimated_pages_with_text"] = pages_with_text
            
            # 计算文本比例（粗略估计）
            if metrics["page_count"] > 0:
                metrics["text_ratio"] = pages_with_text / metrics["page_count"]
            
            return metrics
            
        except Exception as e:
            logger.warning(f"计算质量指标异常: {str(e)}")
            return metrics
    
    def _parse_company_from_filename(self, filename: str) -> Optional[str]:
        """
        从文件名解析公司名称
        
        Args:
            filename: 文件名
            
        Returns:
            解析出的公司名称或None
        """
        # 尝试解析常见的命名格式：公司名称_年份_报告类型.pdf
        try:
            # 移除扩展名
            basename = os.path.splitext(filename)[0]
            
            # 分割下划线
            parts = basename.split('_')
            
            if len(parts) >= 3:
                # 假设格式为: 公司名称_年份_报告类型
                company_part = parts[0]
                
                # 简单的清理
                company_name = company_part.replace("-", "").replace(" ", "")
                
                # 检查是否包含常见报告类型关键词，如果是则不是公司名称
                report_keywords = ["年报", "中报", "annual", "interim"]
                if any(keyword in company_name.lower() for keyword in report_keywords):
                    return None
                
                return company_name
            
            return None
            
        except Exception:
            return None
    
    def generate_report(self, validation_results: Dict, output_format: str = "text") -> str:
        """
        生成验证报告
        
        Args:
            validation_results: 验证结果
            output_format: 输出格式，支持"text", "json", "markdown"
            
        Returns:
            报告内容
        """
        if output_format == "json":
            return json.dumps(validation_results, ensure_ascii=False, indent=2)
        
        elif output_format == "markdown":
            report = "# PDF验证报告\n\n"
            
            if "directory" in validation_results:
                # 批量验证报告
                report += f"## 目录验证统计\n\n"
                report += f"- **验证目录**: {validation_results['directory']}\n"
                report += f"- **验证时间**: {validation_results['validation_time']}\n"
                report += f"- **总文件数**: {validation_results['total_files']}\n"
                report += f"- **通过数**: {validation_results['passed']}\n"
                report += f"- **失败数**: {validation_results['failed']}\n"
                report += f"- **部分匹配**: {validation_results['partial']}\n"
                report += f"- **错误数**: {validation_results['errors']}\n"
                report += f"- **通过率**: {validation_results['passed']/validation_results['total_files']*100:.1f}%\n\n"
                
                if "files" in validation_results:
                    report += "## 文件详细验证结果\n\n"
                    report += "| 文件名 | 文件大小 | 验证结果 | 置信度 | 验证详情 |\n"
                    report += "|--------|----------|----------|--------|----------|\n"
                    
                    for file_result in validation_results["files"]:
                        filename = file_result.get("file_name", "")
                        size_kb = file_result.get("file_size", 0) / 1024
                        result = file_result.get("validation_result", "")
                        confidence = file_result.get("confidence_score", 0) * 100
                        details = file_result.get("validation_details", "")
                        
                        report += f"| {filename} | {size_kb:.1f}KB | {result} | {confidence:.1f}% | {details} |\n"
            
            else:
                # 单个文件验证报告
                report += f"## 文件验证结果\n\n"
                report += f"- **文件名**: {validation_results.get('file_name', '')}\n"
                report += f"- **文件路径**: {validation_results.get('file_path', '')}\n"
                report += f"- **文件大小**: {validation_results.get('file_size', 0) / 1024:.1f}KB\n"
                report += f"- **页数**: {validation_results.get('page_count', 0)}\n"
                report += f"- **验证时间**: {validation_results.get('validation_time', '')}\n"
                report += f"- **验证结果**: **{validation_results.get('validation_result', '')}**\n"
                report += f"- **置信度**: {validation_results.get('confidence_score', 0) * 100:.1f}%\n"
                report += f"- **验证详情**: {validation_results.get('validation_details', '')}\n\n"
                
                if validation_results.get('keywords_found'):
                    report += f"- **匹配关键词**: {', '.join(validation_results['keywords_found'])}\n"
                
                if validation_results.get('first_page_text'):
                    report += f"\n## 第一页内容（前500字符）\n\n"
                    report += f"```text\n{validation_results['first_page_text']}\n```\n"
            
            return report
        
        else:  # text格式
            report = "=" * 60 + "\n"
            report += "PDF验证报告\n"
            report += "=" * 60 + "\n\n"
            
            if "directory" in validation_results:
                # 批量验证报告
                report += f"目录: {validation_results['directory']}\n"
                report += f"时间: {validation_results['validation_time']}\n"
                report += f"文件总数: {validation_results['total_files']}\n"
                report += f"通过: {validation_results['passed']}\n"
                report += f"失败: {validation_results['failed']}\n"
                report += f"部分匹配: {validation_results['partial']}\n"
                report += f"错误: {validation_results['errors']}\n"
                report += f"通过率: {validation_results['passed']/validation_results['total_files']*100:.1f}%\n\n"
                
                if "files" in validation_results:
                    report += "文件详细结果:\n"
                    for file_result in validation_results["files"]:
                        report += f"  - {file_result.get('file_name', '')}: "
                        report += f"{file_result.get('validation_result', '')} "
                        report += f"({file_result.get('confidence_score', 0)*100:.1f}%)\n"
            
            else:
                # 单个文件验证报告
                report += f"文件名: {validation_results.get('file_name', '')}\n"
                report += f"验证结果: {validation_results.get('validation_result', '')}\n"
                report += f"置信度: {validation_results.get('confidence_score', 0)*100:.1f}%\n"
                report += f"详情: {validation_results.get('validation_details', '')}\n"
                
                if validation_results.get('keywords_found'):
                    report += f"关键词: {', '.join(validation_results['keywords_found'])}\n"
                
                if validation_results.get('first_page_text'):
                    report += f"\n第一页内容:\n{validation_results['first_page_text']}\n"
            
            report += "\n" + "=" * 60 + "\n"
            return report


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="财务报告PDF验证工具")
    
    # 输入选项
    parser.add_argument("--file", help="要验证的PDF文件路径")
    parser.add_argument("--dir", help="要验证的目录路径（批量验证）")
    parser.add_argument("--recursive", action="store_true", help="递归搜索子目录（仅与--dir一起使用）")
    
    # 验证选项
    parser.add_argument("--company", help="公司名称（用于验证匹配）")
    parser.add_argument("--report-type", choices=["annual", "interim"], 
                       help="报告类型：annual（年报）或interim（中报）")
    
    # 输出选项
    parser.add_argument("--output", choices=["text", "json", "markdown"], 
                       default="text", help="输出格式")
    parser.add_argument("--output-file", help="输出到文件（默认输出到控制台）")
    
    args = parser.parse_args()
    
    # 检查输入
    if not args.file and not args.dir:
        parser.error("必须指定--file或--dir参数")
    
    # 创建验证器
    validator = PDFValidator()
    
    # 执行验证
    if args.file:
        # 单个文件验证
        result = validator.validate_file(args.file, args.company, args.report_type)
    else:
        # 批量验证目录
        result = validator.validate_directory(args.dir, args.recursive, args.report_type)
    
    # 生成报告
    report = validator.generate_report(result, args.output)
    
    # 输出报告
    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"验证报告已保存到: {args.output_file}")
    else:
        print(report)
    
    # 返回退出码（如果有失败则返回1）
    if "failed" in result and result["failed"] > 0:
        return 1
    elif "validation_result" in result and result["validation_result"] == "FAIL":
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
