#!/usr/bin/env python3
"""
公司财务报告搜索辅助脚本
功能：提供常见公司财报来源的搜索建议和URL构造方法
注意：实际搜索需要结合WebSearch工具，本脚本主要提供搜索策略和模板
"""

import re
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime


class CompanyReportSearcher:
    """公司报告搜索器"""
    
    def __init__(self):
        """初始化搜索器"""
        # 公司信息数据库（示例数据）
        self.company_database = {
            "海尔智家": {
                "stock_code": "600690",
                "market": "A股",
                "exchange": "上海证券交易所",
                "english_name": "Haier Smart Home Co., Ltd.",
                "website": "https://www.haier.com",
                "ir_section": "https://www.haier.com/investor-relations/",
                "report_patterns": [
                    "https://file.finance.qq.com/finance/hs/pdf/{year}/{month}/{day}/*.PDF",
                    "https://stockmc.xueqiu.com/{year}/{stock_code}_*.pdf"
                ]
            },
            "安贤园中国": {
                "stock_code": "00922",
                "market": "港股",
                "exchange": "香港交易所",
                "english_name": "ANXIAN YUAN CHINA HOLDINGS LIMITED",
                "traditional_name": "安賢園中國控股有限公司",
                "website": "https://www.anxianyuanchina.com",
                "ir_section": "https://www.anxianyuanchina.com/data/upload/finanical/",
                "report_patterns": [
                    "https://www.anxianyuanchina.com/data/upload/finanical/{lang}/{date}/cw_{stock_code}Ann-{date}-{date}.pdf",
                    "https://www.anxianyuanchina.com/data/upload/finanical/{lang}/{date}/ew_{stock_code}Ann-{date}-{date}.pdf"
                ]
            }
        }
        
        # 搜索来源配置
        self.search_sources = {
            "A股": {
                "priority": [
                    {
                        "name": "巨潮资讯网",
                        "url_template": "https://www.cninfo.com.cn/new/disclosure/stock?orgId={stock_code}&stockCode={stock_code}",
                        "search_keywords": ["{company} {year} 年年度报告", "{company} {year} 年报"]
                    },
                    {
                        "name": "公司官网投资者关系",
                        "url_template": None,  # 需要具体公司URL
                        "search_keywords": ["{company} 投资者关系", "{company} 财务报告", "{english_name} investor relations"]
                    },
                    {
                        "name": "雪球",
                        "url_template": "https://xueqiu.com/S/{stock_code}",
                        "search_keywords": ["{stock_code} {year} 年报", "{company} {year} 年报 PDF"]
                    },
                    {
                        "name": "腾讯财经",
                        "url_template": "https://gu.qq.com/{stock_code}",
                        "search_keywords": ["{company} {year} 年报 文件", "{stock_code} {year} 年度报告"]
                    }
                ]
            },
            "港股": {
                "priority": [
                    {
                        "name": "香港交易所披露易",
                        "url_template": "https://www1.hkexnews.hk/search/titlesearch.xhtml?search_type=1&stock_code={stock_code}&category=1&document_type=-1&from_date=&to_date=&lang=ZH",
                        "search_keywords": ["股份代号 {stock_code} 财务报告", "{stock_code} {year} Annual Report"]
                    },
                    {
                        "name": "公司官网投资者关系",
                        "url_template": None,
                        "search_keywords": ["{company} 财务报告", "{english_name} Annual Report", "{traditional_name} 年報"]
                    },
                    {
                        "name": "AASTOCKS",
                        "url_template": "https://www.aastocks.com/tc/stocks/analysis/company-fundamental/financial-summary?symbol={stock_code}",
                        "search_keywords": ["{stock_code} 年報", "{company} {year} 業績"]
                    }
                ]
            }
        }
        
        # 报告类型关键词
        self.report_type_keywords = {
            "annual": {
                "zh": ["年报", "年度报告", "全年业绩"],
                "zh_Hant": ["年報", "全年業績", "年度業績"],
                "en": ["Annual Report", "Annual Results"]
            },
            "interim": {
                "zh": ["中报", "中期报告", "半年度报告", "中期业绩"],
                "zh_Hant": ["中期報告", "半年度報告", "中期業績"],
                "en": ["Interim Report", "Interim Results", "Half-year Report"]
            }
        }
    
    def get_company_info(self, company_name: str, stock_code: Optional[str] = None) -> Optional[Dict]:
        """
        获取公司信息
        
        Args:
            company_name: 公司名称
            stock_code: 股票代码（可选）
            
        Returns:
            公司信息字典或None
        """
        # 优先按公司名称查找
        if company_name in self.company_database:
            return self.company_database[company_name]
        
        # 如果提供了股票代码，尝试查找匹配的公司
        if stock_code:
            for name, info in self.company_database.items():
                if info["stock_code"] == stock_code:
                    return info
        
        # 未找到公司信息，返回基本结构
        return {
            "stock_code": stock_code or "未知",
            "market": self._guess_market(stock_code) if stock_code else "未知",
            "exchange": "未知",
            "english_name": company_name,  # 默认使用中文名
            "website": None,
            "ir_section": None,
            "report_patterns": []
        }
    
    def generate_search_queries(self, company_name: str, year: int, 
                               report_type: str, stock_code: Optional[str] = None) -> List[Dict]:
        """
        生成搜索查询
        
        Args:
            company_name: 公司名称
            year: 年份
            report_type: 报告类型（"annual"或"interim"）
            stock_code: 股票代码（可选）
            
        Returns:
            搜索查询列表
        """
        # 获取公司信息
        company_info = self.get_company_info(company_name, stock_code)
        market = company_info["market"]
        
        if market not in self.search_sources:
            market = "A股"  # 默认使用A股搜索策略
        
        queries = []
        
        # 获取报告类型关键词
        report_keywords = self.report_type_keywords.get(report_type, {})
        
        # 为每个搜索来源生成查询
        for source in self.search_sources[market]["priority"]:
            # 生成关键词列表
            keywords = []
            
            for keyword_template in source.get("search_keywords", []):
                # 替换模板变量
                keyword = keyword_template.format(
                    company=company_name,
                    stock_code=company_info["stock_code"],
                    english_name=company_info.get("english_name", company_name),
                    traditional_name=company_info.get("traditional_name", company_name),
                    year=year
                )
                
                # 添加报告类型关键词变体
                for lang in ["zh", "zh_Hant", "en"]:
                    if lang in report_keywords:
                        for report_keyword in report_keywords[lang]:
                            keyword_with_type = f"{keyword} {report_keyword}"
                            keywords.append(keyword_with_type)
            
            # 添加基础关键词（不带报告类型）
            for keyword_template in source.get("search_keywords", []):
                keyword = keyword_template.format(
                    company=company_name,
                    stock_code=company_info["stock_code"],
                    english_name=company_info.get("english_name", company_name),
                    traditional_name=company_info.get("traditional_name", company_name),
                    year=year
                )
                keywords.append(keyword)
            
            # 去重
            keywords = list(set(keywords))
            
            # 生成URL模板（如果可用）
            url_template = source.get("url_template")
            direct_url = None
            if url_template:
                try:
                    direct_url = url_template.format(
                        stock_code=company_info["stock_code"],
                        company=company_name
                    )
                except Exception:
                    direct_url = None
            
            queries.append({
                "source": source["name"],
                "market": market,
                "keywords": keywords,
                "direct_url": direct_url,
                "priority": "high" if source["name"] in ["巨潮资讯网", "香港交易所披露易"] else "medium"
            })
        
        return queries
    
    def generate_direct_urls(self, company_name: str, year: int, 
                           report_type: str, stock_code: Optional[str] = None) -> List[Dict]:
        """
        生成直接URL（基于已知的URL模式）
        
        Args:
            company_name: 公司名称
            year: 年份
            report_type: 报告类型
            stock_code: 股票代码
            
        Returns:
            直接URL列表
        """
        company_info = self.get_company_info(company_name, stock_code)
        urls = []
        
        # 检查公司是否有预定义的报告模式
        for pattern in company_info.get("report_patterns", []):
            try:
                # 替换变量
                url = pattern
                
                # 替换股票代码
                if "{stock_code}" in url:
                    url = url.replace("{stock_code}", company_info["stock_code"])
                
                # 替换年份
                if "{year}" in url:
                    url = url.replace("{year}", str(year))
                
                # 替换日期（使用年份的第一天和最后一天作为示例）
                if "{date}" in url:
                    # 使用年份+月份+日期的格式
                    date_str = f"{year}1231" if report_type == "annual" else f"{year}0630"
                    url = url.replace("{date}", date_str)
                
                # 替换语言
                if "{lang}" in url:
                    # 根据市场选择语言
                    if company_info["market"] == "港股":
                        url = url.replace("{lang}", "en")  # 英文版
                    else:
                        url = url.replace("{lang}", "sc")  # 简体中文版
                
                urls.append({
                    "url": url,
                    "source": "公司预定义模式",
                    "company": company_name,
                    "year": year,
                    "report_type": report_type,
                    "confidence": "high" if company_name in self.company_database else "low"
                })
                
            except Exception as e:
                print(f"生成URL失败: {pattern}, 错误: {e}")
        
        return urls
    
    def suggest_search_strategy(self, company_name: str, stock_code: Optional[str] = None) -> Dict:
        """
        建议搜索策略
        
        Args:
            company_name: 公司名称
            stock_code: 股票代码
            
        Returns:
            搜索策略建议
        """
        company_info = self.get_company_info(company_name, stock_code)
        market = company_info["market"]
        
        strategy = {
            "company": company_name,
            "stock_code": company_info["stock_code"],
            "market": market,
            "recommended_sources": [],
            "search_tips": [],
            "common_issues": [],
            "verification_hints": []
        }
        
        # 根据市场推荐搜索来源
        if market == "A股":
            strategy["recommended_sources"] = [
                "巨潮资讯网（官方披露平台）",
                "公司官网投资者关系页面",
                "雪球（xueqiu.com）",
                "腾讯财经（finance.qq.com）"
            ]
            strategy["search_tips"] = [
                f"搜索关键词：'{company_name} {datetime.now().year} 年年度报告'",
                f"添加'filetype:pdf'限定PDF文件",
                f"使用公司股票代码'{company_info['stock_code']}'替代公司名称搜索"
            ]
            strategy["common_issues"] = [
                "A股公司报告通常在巨潮资讯网统一披露",
                "注意区分年度报告和业绩预告",
                "优先选择官方来源，避免第三方转发的版本"
            ]
            strategy["verification_hints"] = [
                f"验证第一页是否包含'{company_name}'",
                "检查是否为'年度报告'而非'业绩预告'",
                "确认报告年份与请求一致"
            ]
            
        elif market == "港股":
            strategy["recommended_sources"] = [
                "香港交易所披露易（hkexnews.hk）",
                "公司官网投资者关系页面",
                "AASTOCKS（aastocks.com）"
            ]
            strategy["search_tips"] = [
                f"搜索关键词：'{company_name} {datetime.now().year} Annual Report'",
                f"使用繁体中文：'{company_info.get('traditional_name', company_name)} {datetime.now().year} 年報'",
                f"添加'PDF'关键字限定文件类型"
            ]
            strategy["common_issues"] = [
                "港股公司报告通常有中英文双语版本",
                "注意财务年度可能不是自然年度（如4月1日-次年3月31日）",
                "披露易网站可能需要处理验证码"
            ]
            strategy["verification_hints"] = [
                f"验证第一页是否包含'{company_name}'或'{company_info.get('english_name', '')}'",
                "检查是否为'Annual Report'或'年報'",
                "确认报告期间与请求年份一致"
            ]
        
        # 如果有公司官网，添加具体建议
        if company_info.get("website"):
            strategy["search_tips"].append(f"访问公司官网: {company_info['website']}")
            if company_info.get("ir_section"):
                strategy["search_tips"].append(f"投资者关系页面: {company_info['ir_section']}")
        
        return strategy
    
    def parse_filename_for_info(self, filename: str) -> Dict:
        """
        从文件名解析公司信息
        
        Args:
            filename: 文件名
            
        Returns:
            解析出的信息
        """
        info = {
            "company_name": None,
            "year": None,
            "report_type": None,
            "stock_code": None,
            "confidence": "low"
        }
        
        # 常见文件名模式
        patterns = [
            # 公司名称_年份_报告类型.pdf
            r"^(.*?)_(\d{4})_(年报|中报|annual|interim)\.pdf$",
            # 公司名称_股票代码_年份_报告类型.pdf
            r"^(.*?)_(\d{5,6})_(\d{4})_(年报|中报)\.pdf$",
            # 股票代码_年份_报告类型.pdf
            r"^(\d{5,6})_(\d{4})_(年报|中报|Annual|Interim)\.pdf$"
        ]
        
        for pattern in patterns:
            match = re.match(pattern, filename, re.IGNORECASE)
            if match:
                groups = match.groups()
                
                if len(groups) == 3:
                    # 模式1: 公司名称_年份_报告类型
                    info["company_name"] = groups[0]
                    info["year"] = int(groups[1])
                    info["report_type"] = self._normalize_report_type(groups[2])
                    info["confidence"] = "medium"
                    
                elif len(groups) == 4:
                    # 模式2: 公司名称_股票代码_年份_报告类型
                    info["company_name"] = groups[0]
                    info["stock_code"] = groups[1]
                    info["year"] = int(groups[2])
                    info["report_type"] = self._normalize_report_type(groups[3])
                    info["confidence"] = "high"
                    
                elif len(groups) == 3 and groups[0].isdigit():
                    # 模式3: 股票代码_年份_报告类型
                    info["stock_code"] = groups[0]
                    info["year"] = int(groups[1])
                    info["report_type"] = self._normalize_report_type(groups[2])
                    info["confidence"] = "medium"
                
                break
        
        return info
    
    def _guess_market(self, stock_code: str) -> str:
        """
        根据股票代码猜测市场
        
        Args:
            stock_code: 股票代码
            
        Returns:
            市场类型（"A股"或"港股"）
        """
        if not stock_code:
            return "未知"
        
        # A股代码：6位数字，6开头（上海）或0、3开头（深圳）
        if len(stock_code) == 6 and stock_code.isdigit():
            if stock_code.startswith(('6', '0', '3')):
                return "A股"
        
        # 港股代码：5位数字，0开头
        if len(stock_code) == 5 and stock_code.isdigit() and stock_code.startswith('0'):
            return "港股"
        
        return "未知"
    
    def _normalize_report_type(self, report_type: str) -> str:
        """
        标准化报告类型
        
        Args:
            report_type: 报告类型字符串
            
        Returns:
            标准化后的报告类型（"annual"或"interim"）
        """
        report_type_lower = report_type.lower()
        
        if report_type_lower in ["年报", "annual", "年報"]:
            return "annual"
        elif report_type_lower in ["中报", "interim", "中期", "中期報告"]:
            return "interim"
        else:
            return "unknown"


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="公司财务报告搜索辅助工具")
    
    # 模式选择
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # 生成搜索查询命令
    query_parser = subparsers.add_parser("query", help="生成搜索查询")
    query_parser.add_argument("--company", required=True, help="公司名称")
    query_parser.add_argument("--year", type=int, required=True, help="年份")
    query_parser.add_argument("--report-type", choices=["annual", "interim"], required=True, help="报告类型")
    query_parser.add_argument("--code", help="股票代码")
    
    # 解析文件名命令
    parse_parser = subparsers.add_parser("parse", help="从文件名解析信息")
    parse_parser.add_argument("--filename", required=True, help="文件名")
    
    # 建议策略命令
    strategy_parser = subparsers.add_parser("strategy", help="获取搜索策略建议")
    strategy_parser.add_argument("--company", required=True, help="公司名称")
    strategy_parser.add_argument("--code", help="股票代码")
    
    args = parser.parse_args()
    
    searcher = CompanyReportSearcher()
    
    if args.command == "query":
        queries = searcher.generate_search_queries(
            company_name=args.company,
            year=args.year,
            report_type=args.report_type,
            stock_code=args.code
        )
        
        print("搜索查询生成完成：")
        print("=" * 60)
        
        for i, query in enumerate(queries, 1):
            print(f"\n{i}. 来源: {query['source']} ({query['market']})")
            print(f"   优先级: {query['priority']}")
            
            if query['direct_url']:
                print(f"   直接访问: {query['direct_url']}")
            
            print(f"   搜索关键词:")
            for keyword in query['keywords'][:5]:  # 只显示前5个关键词
                print(f"     - {keyword}")
            
            if len(query['keywords']) > 5:
                print(f"     ... 还有 {len(query['keywords']) - 5} 个关键词")
        
        print("\n" + "=" * 60)
        
        # 同时生成直接URL（如果有）
        direct_urls = searcher.generate_direct_urls(
            company_name=args.company,
            year=args.year,
            report_type=args.report_type,
            stock_code=args.code
        )
        
        if direct_urls:
            print("\n直接URL（基于已知模式）：")
            for url_info in direct_urls:
                print(f"  - {url_info['url']} ({url_info['confidence']} confidence)")
    
    elif args.command == "parse":
        info = searcher.parse_filename_for_info(args.filename)
        
        print("文件名解析结果：")
        print(f"  文件名: {args.filename}")
        print(f"  公司名称: {info['company_name'] or '未知'}")
        print(f"  股票代码: {info['stock_code'] or '未知'}")
        print(f"  年份: {info['year'] or '未知'}")
        print(f"  报告类型: {info['report_type'] or '未知'}")
        print(f"  置信度: {info['confidence']}")
    
    elif args.command == "strategy":
        strategy = searcher.suggest_search_strategy(args.company, args.code)
        
        print("搜索策略建议：")
        print("=" * 60)
        print(f"公司: {strategy['company']}")
        print(f"股票代码: {strategy['stock_code']}")
        print(f"市场: {strategy['market']}")
        
        print("\n推荐搜索来源：")
        for source in strategy['recommended_sources']:
            print(f"  • {source}")
        
        print("\n搜索技巧：")
        for tip in strategy['search_tips']:
            print(f"  • {tip}")
        
        print("\n常见问题：")
        for issue in strategy['common_issues']:
            print(f"  • {issue}")
        
        print("\n验证提示：")
        for hint in strategy['verification_hints']:
            print(f"  • {hint}")
        
        print("=" * 60)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()