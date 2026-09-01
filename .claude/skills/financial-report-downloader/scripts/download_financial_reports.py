#!/usr/bin/env python3
"""
财务报告下载主脚本
功能：从网络搜索、下载、验证并规范化存储上市公司财务报告PDF文件
支持：A股（沪深交易所）、港股（香港交易所）上市公司年度报告和中期报告

PDF后端支持（按优先级）：PyMuPDF > pypdf > pdfminer.six
"""

import os
import sys
import argparse
import logging
import json
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import subprocess
import tempfile
import shutil

# 添加项目根目录到路径，以便导入其他模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入兼容层
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('financial_report_downloader.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class FinancialReportDownloader:
    """财务报告下载器主类"""
    
    def __init__(self, output_dir: str = "company_analysis"):
        """
        初始化下载器
        
        Args:
            output_dir: 输出目录，默认为company_analysis
        """
        self.output_dir = output_dir
        self.download_log = []
        self.validation_results = []
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
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
        
        logger.info(f"财务报告下载器初始化完成，输出目录：{output_dir}，PDF后端：{get_pdf_backend()}")
    
    def download_pdf(self, url: str, temp_path: str) -> Tuple[bool, str, int]:
        """
        下载PDF文件
        
        Args:
            url: PDF文件URL
            temp_path: 临时文件路径
            
        Returns:
            (成功标志, 错误信息, 文件大小)
        """
        try:
            # 使用curl下载文件，支持重定向
            cmd = ['curl', '-L', '-s', '-o', temp_path, url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                return False, f"curl命令失败: {result.stderr}", 0
            
            # 检查文件是否存在且大小合理
            if not os.path.exists(temp_path):
                return False, "下载文件不存在", 0
            
            file_size = os.path.getsize(temp_path)
            if file_size < 1024:  # 小于1KB可能是错误页面
                os.remove(temp_path)
                return False, f"文件大小异常: {file_size}字节", file_size
            
            logger.info(f"PDF下载成功: {url} -> {temp_path} ({file_size}字节)")
            return True, "", file_size
            
        except subprocess.TimeoutExpired:
            return False, "下载超时", 0
        except Exception as e:
            return False, f"下载异常: {str(e)}", 0
    
    def validate_pdf(self, pdf_path: str, company_name: str, report_type: str) -> Tuple[bool, str, Dict]:
        """
        验证PDF文件内容
        
        Args:
            pdf_path: PDF文件路径
            company_name: 公司名称
            report_type: 报告类型（annual/interim）
            
        Returns:
            (验证通过, 验证详情, 验证结果数据)
        """
        validation_result = {
            "company_match": False,
            "report_type_match": False,
            "exclusion_match": False,
            "keywords_found": [],
            "first_page_text": "",
            "confidence_score": 0.0
        }
        
        try:
            # 打开PDF文件并提取第一页文本
            with open_pdf(pdf_path) as pdf:
                if len(pdf.pages) == 0:
                    return False, "PDF文件无页面", validation_result
                
                first_page = pdf.pages[0]
                text = first_page.extract_text()
                if not text:
                    return False, "无法提取文本（可能是扫描版）", validation_result
                
                # 保存第一页文本（前500字符）
                validation_result["first_page_text"] = text[:500] + "..." if len(text) > 500 else text
                text_lower = text.lower()
                
                # 生成公司名称关键词
                company_keywords = self._generate_company_keywords(company_name)
                
                # 验证公司名称
                company_found = False
                for keyword in company_keywords:
                    if keyword.lower() in text_lower:
                        company_found = True
                        validation_result["keywords_found"].append(f"公司:{keyword}")
                        break
                
                validation_result["company_match"] = company_found
                
                # 验证报告类型
                report_type_found = False
                report_type_key = "annual" if report_type in ["年报", "annual"] else "interim"
                
                for lang in ["zh", "zh_Hant", "en"]:
                    if lang in self.report_keywords[report_type_key]:
                        for keyword in self.report_keywords[report_type_key][lang]:
                            if keyword.lower() in text_lower:
                                report_type_found = True
                                validation_result["keywords_found"].append(f"报告:{keyword}")
                                break
                    if report_type_found:
                        break
                
                validation_result["report_type_match"] = report_type_found
                
                # 检查排除关键词
                exclusion_found = False
                for lang in ["zh", "en"]:
                    if lang in self.exclusion_keywords:
                        for keyword in self.exclusion_keywords[lang]:
                            if keyword.lower() in text_lower:
                                exclusion_found = True
                                validation_result["keywords_found"].append(f"排除:{keyword}")
                                break
                    if exclusion_found:
                        break
                
                validation_result["exclusion_match"] = exclusion_found
                
                # 计算置信度分数
                confidence = 0.0
                if company_found:
                    confidence += 0.5
                if report_type_found:
                    confidence += 0.4
                if not exclusion_found:
                    confidence += 0.1
                
                validation_result["confidence_score"] = confidence
                
                # 最终验证决策
                if company_found and report_type_found and not exclusion_found:
                    return True, "验证通过", validation_result
                else:
                    reason = []
                    if not company_found:
                        reason.append("公司名称不匹配")
                    if not report_type_found:
                        reason.append("报告类型不匹配")
                    if exclusion_found:
                        reason.append("包含排除关键词")
                    return False, f"验证失败: {', '.join(reason)}", validation_result
                    
        except Exception as e:
            return False, f"验证异常: {str(e)}", validation_result
    
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
    
    def create_directory_structure(self, company_name: str, stock_code: str) -> str:
        """
        创建目录结构
        
        Args:
            company_name: 公司名称
            stock_code: 股票代码
            
        Returns:
            创建的目录路径
        """
        # 清理公司名称中的特殊字符
        safe_company_name = company_name.replace("/", "_").replace("\\", "_").replace(":", "_")
        dir_name = f"{safe_company_name}_{stock_code}"
        dir_path = os.path.join(self.output_dir, dir_name)
        
        os.makedirs(dir_path, exist_ok=True)
        logger.info(f"创建目录: {dir_path}")
        
        return dir_path
    
    def save_download_log(self):
        """保存下载日志"""
        log_file = os.path.join(self.output_dir, "download_log.md")
        
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("# 财务报告下载清单\n\n")
            f.write(f"## 下载统计\n")
            f.write(f"- 下载时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- 总下载数: {len(self.download_log)}\n")
            
            success_count = sum(1 for item in self.download_log if item.get("success", False))
            f.write(f"- 成功数: {success_count}\n")
            f.write(f"- 失败数: {len(self.download_log) - success_count}\n")
            f.write(f"- 成功率: {success_count/len(self.download_log)*100:.1f}%\n\n")
            
            f.write("## 文件清单\n")
            f.write("| 公司名称 | 股票代码 | 报告类型 | 年份 | 文件名 | 文件大小 | 下载来源 | 验证状态 | 下载时间 |\n")
            f.write("|---------|---------|---------|------|--------|----------|----------|----------|----------|\n")
            
            for item in self.download_log:
                company = item.get("company", "")
                code = item.get("stock_code", "")
                report_type = item.get("report_type", "")
                year = item.get("year", "")
                filename = item.get("filename", "")
                size = item.get("file_size", 0)
                source = item.get("source", "")[:50] + "..." if len(item.get("source", "")) > 50 else item.get("source", "")
                status = "✅ 通过" if item.get("success", False) else "❌ 失败"
                download_time = item.get("download_time", "")
                
                size_str = f"{size/1024:.1f}KB" if size < 1024*1024 else f"{size/(1024*1024):.1f}MB"
                
                f.write(f"| {company} | {code} | {report_type} | {year} | {filename} | {size_str} | {source} | {status} | {download_time} |\n")
        
        logger.info(f"下载日志已保存: {log_file}")
    
    def save_validation_report(self):
        """保存验证报告"""
        report_file = os.path.join(self.output_dir, "validation_report.md")
        
        with open(report_file, "w", encoding="utf-8") as f:
            f.write("# 财务报告验证报告\n\n")
            f.write(f"## 验证统计\n")
            f.write(f"- 验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- 总验证文件数: {len(self.validation_results)}\n")
            
            pass_count = sum(1 for item in self.validation_results if item.get("validation_passed", False))
            f.write(f"- 验证通过: {pass_count}\n")
            f.write(f"- 验证失败: {len(self.validation_results) - pass_count}\n")
            f.write(f"- 验证通过率: {pass_count/len(self.validation_results)*100:.1f}%\n\n")
            
            f.write("## 详细验证记录\n")
            
            f.write("### 通过文件\n")
            pass_items = [item for item in self.validation_results if item.get("validation_passed", False)]
            for i, item in enumerate(pass_items, 1):
                f.write(f"{i}. **文件名**: {item.get('filename', '')}\n")
                f.write(f"   - **验证详情**: {item.get('validation_details', '')}\n")
                f.write(f"   - **置信度**: {item.get('confidence_score', 0)*100:.1f}%\n")
                f.write(f"   - **关键词**: {', '.join(item.get('keywords_found', []))}\n\n")
            
            f.write("### 失败文件\n")
            fail_items = [item for item in self.validation_results if not item.get("validation_passed", False)]
            for i, item in enumerate(fail_items, 1):
                f.write(f"{i}. **文件名**: {item.get('filename', '')}\n")
                f.write(f"   - **失败原因**: {item.get('validation_details', '')}\n")
                f.write(f"   - **第一页内容**: {item.get('first_page_text', '')}\n\n")
        
        logger.info(f"验证报告已保存: {report_file}")
    
    def download_report(self, company_name: str, stock_code: str, year: int, 
                        report_type: str, source_url: str) -> Dict:
        """
        下载单个财务报告
        
        Args:
            company_name: 公司名称
            stock_code: 股票代码
            year: 年份
            report_type: 报告类型（年报/中报）
            source_url: 下载来源URL
            
        Returns:
            下载结果字典
        """
        result = {
            "company": company_name,
            "stock_code": stock_code,
            "year": year,
            "report_type": report_type,
            "source": source_url,
            "success": False,
            "filename": "",
            "file_size": 0,
            "validation_passed": False,
            "validation_details": "",
            "download_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                temp_path = tmp_file.name
            
            # 下载PDF文件
            download_success, download_error, file_size = self.download_pdf(source_url, temp_path)
            
            if not download_success:
                result["validation_details"] = f"下载失败: {download_error}"
                self.download_log.append(result)
                os.unlink(temp_path)
                return result
            
            result["file_size"] = file_size
            
            # 验证PDF文件
            report_type_key = "annual" if report_type in ["年报", "annual"] else "interim"
            validation_passed, validation_details, validation_data = self.validate_pdf(
                temp_path, company_name, report_type_key
            )
            
            result["validation_passed"] = validation_passed
            result["validation_details"] = validation_details
            result.update(validation_data)
            
            if not validation_passed:
                result["success"] = False
                self.download_log.append(result)
                self.validation_results.append(result)
                os.unlink(temp_path)
                return result
            
            # 创建目录结构
            target_dir = self.create_directory_structure(company_name, stock_code)
            
            # 生成目标文件名
            safe_company_name = company_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            filename = f"{safe_company_name}_{year}_{report_type}.pdf"
            target_path = os.path.join(target_dir, filename)
            
            # 移动文件到目标目录
            shutil.move(temp_path, target_path)
            
            result["success"] = True
            result["filename"] = filename
            
            logger.info(f"报告下载完成: {filename} -> {target_path}")
            
        except Exception as e:
            result["success"] = False
            result["validation_details"] = f"处理异常: {str(e)}"
            logger.error(f"下载报告异常: {str(e)}")
        
        # 记录结果
        self.download_log.append(result)
        self.validation_results.append(result)
        
        return result
    
    def batch_download(self, tasks: List[Dict]):
        """
        批量下载多个报告
        
        Args:
            tasks: 任务列表，每个任务包含company_name, stock_code, year, report_type, source_url
        """
        logger.info(f"开始批量下载，共{len(tasks)}个任务")
        
        for i, task in enumerate(tasks, 1):
            logger.info(f"处理任务 {i}/{len(tasks)}: {task.get('company_name')} {task.get('year')} {task.get('report_type')}")
            
            result = self.download_report(
                company_name=task.get("company_name"),
                stock_code=task.get("stock_code"),
                year=task.get("year"),
                report_type=task.get("report_type"),
                source_url=task.get("source_url")
            )
            
            if result["success"]:
                logger.info(f"✓ 任务成功: {result['filename']}")
            else:
                logger.warning(f"✗ 任务失败: {result['validation_details']}")
            
            # 避免请求过于频繁
            time.sleep(1)
        
        # 保存日志和报告
        self.save_download_log()
        self.save_validation_report()
        
        logger.info("批量下载完成")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="财务报告下载器")
    parser.add_argument("--company", required=True, help="公司名称")
    parser.add_argument("--code", required=True, help="股票代码")
    parser.add_argument("--year", type=int, required=True, help="年份")
    parser.add_argument("--report-type", required=True, choices=["年报", "中报"], help="报告类型")
    parser.add_argument("--source", required=True, help="PDF文件URL")
    parser.add_argument("--output-dir", default="company_analysis", help="输出目录")
    
    args = parser.parse_args()
    
    # 创建下载器
    downloader = FinancialReportDownloader(output_dir=args.output_dir)
    
    # 下载报告
    result = downloader.download_report(
        company_name=args.company,
        stock_code=args.code,
        year=args.year,
        report_type=args.report_type,
        source_url=args.source
    )
    
    # 输出结果
    if result["success"]:
        print(f"✓ 下载成功: {result['filename']}")
        print(f"   文件大小: {result['file_size']}字节")
        print(f"   验证置信度: {result['confidence_score']*100:.1f}%")
    else:
        print(f"✗ 下载失败: {result['validation_details']}")
    
    # 保存日志
    downloader.save_download_log()
    downloader.save_validation_report()
    
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
