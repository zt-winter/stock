# 财务报告验证标准

## 验证概述

财务报告PDF文件的验证是确保下载文件为目标公司正式报告的关键步骤。验证基于第一页文本内容，检查是否包含公司名称和报告类型的关键词。

## 验证流程

### 1. 文本提取
- 使用pdf_helper兼容层（支持PyMuPDF/pypdf/pdfminer.six）打开PDF文件
- 提取第一页文本内容（前1000字符）
- 文本预处理：转换为小写，去除多余空格和标点

### 2. 关键词匹配
- 公司名称关键词匹配
- 报告类型关键词匹配
- 排除关键词检查

### 3. 验证决策
- **通过**：公司名称和报告类型同时匹配
- **失败**：任一条件不匹配或匹配排除关键词
- **不确定**：需要人工检查的情况

## 公司名称匹配标准

### A股公司名称关键词

#### 基础关键词
- 用户提供的公司名称（如"海尔智家"）
- 公司股票简称（如"海尔智家"）
- 公司全称（如"海尔智家股份有限公司"）

#### 变体处理
- **简体中文**：原始名称
- **英文名称**：如"Haier Smart Home Co., Ltd."
- **特殊字符**：括号、空格等统一处理

#### 匹配规则
- 部分匹配：文本中包含公司名称关键词即可
- 忽略大小写：统一转换为小写比较
- 允许变体：接受公司全称、简称等变体

### 港股公司名称关键词

#### 基础关键词
- 中文名称（如"安贤园中国"）
- 繁体名称（如"安賢園中國"）
- 英文名称（如"ANXIAN YUAN CHINA HOLDINGS LIMITED"）
- 公司全称（如"安賢園中國控股有限公司"）

#### 特殊处理
- **中英文混合**：接受中英文混合名称
- **括号内容**：忽略注册地等信息，如"(於百慕達註冊成立之有限公司)"
- **股票代码**：可包含股票代码，如"(股份代號：00922)"

#### 匹配规则
- 多语言支持：同时检查中文和英文关键词
- 变体扩展：自动生成常见变体进行匹配
- 容错匹配：允许部分字符差异

### 公司名称关键词生成算法

#### 1. 基础关键词生成
```python
def generate_company_keywords(company_name):
    keywords = [company_name]
    
    # 简体转繁体（港股公司）
    if contains_chinese(company_name):
        keywords.append(simplified_to_traditional(company_name))
    
    # 添加"有限公司"、"股份有限公司"后缀
    if not company_name.endswith("公司"):
        keywords.append(company_name + "有限公司")
        keywords.append(company_name + "股份有限公司")
    
    return keywords
```

#### 2. 变体扩展规则
- 移除空格：`"海尔智家"` → `"海尔智家"`
- 移除特殊字符：`"A/B公司"` → `"AB公司"`
- 常见后缀：添加`"集团"`、`"控股"`等

#### 3. 英文名称处理
- 全大写：`"Haier Smart Home"` → `"HAIER SMART HOME"`
- 缩写形式：`"Co., Ltd."` → `"Co Ltd"`
- 忽略冠词：移除`"The"`、`"A"`、`"An"`等

## 报告类型匹配标准

### 年度报告关键词

#### 中文关键词
1. **基础关键词**：
   - `年报`
   - `年度报告`
   - `年報`（繁体）

2. **扩展关键词**：
   - `全年業績`（港股常见）
   - `全年业绩`
   - `年度業績`
   - `年度业绩`

3. **完整表述**：
   - `截至...止年度全年業績公佈`
   - `...年度报告`
   - `...年年度报告`

#### 英文关键词
1. **基础关键词**：
   - `Annual Report`
   - `Annual`

2. **扩展关键词**：
   - `Financial Statements`
   - `Annual Financial Report`
   - `Annual Results`

3. **完整表述**：
   - `Annual Report for the year ended...`
   - `Annual Results Announcement`

### 中期报告关键词

#### 中文关键词
1. **基础关键词**：
   - `中报`
   - `中期报告`
   - `半年度报告`
   - `中期業績`（繁体）
   - `中期业绩`

2. **扩展关键词**：
   - `半年度業績`
   - `半年度业绩`
   - `中期財務報告`

3. **完整表述**：
   - `截至...止六個月中期業績`
   - `...年中期报告`
   - `...年半年度报告`

#### 英文关键词
1. **基础关键词**：
   - `Interim Report`
   - `Interim`
   - `Half-year Report`

2. **扩展关键词**：
   - `Interim Results`
   - `Half-year Results`
   - `Interim Financial Report`

3. **完整表述**：
   - `Interim Results for the six months ended...`
   - `Half-year Report for the period ended...`

### 报告类型匹配算法

#### 1. 关键词权重设置
```python
report_keywords = {
    "annual": {
        "high_weight": ["年度报告", "Annual Report", "年報"],
        "medium_weight": ["年报", "Annual", "全年業績"],
        "low_weight": ["年度", "全年业绩"]
    },
    "interim": {
        "high_weight": ["中期报告", "Interim Report", "中期業績"],
        "medium_weight": ["中报", "Interim", "半年度報告"],
        "low_weight": ["中期", "半年度", "Half-year"]
    }
}
```

#### 2. 匹配评分规则
- 高权重关键词：3分
- 中权重关键词：2分
- 低权重关键词：1分
- 总分阈值：≥2分视为匹配

#### 3. 多关键词组合
- 同一类别多个关键词匹配时取最高分
- 不同类别关键词可累加
- 排除关键词匹配时直接否决

## 排除标准

### 排除关键词列表

#### 1. 补充报告类
- `补充`
- `补充公告`
- `补充报告`
- `Supplement`
- `Supplementary`
- `Addendum`

#### 2. 业绩预告类
- `业绩预告`
- `盈利预告`
- `Profit Alert`
- `Trading Update`
- `预先披露`

#### 3. 演示材料类
- `演示稿`
- `Presentation`
- `Briefing`
- `演示材料`
- `说明会`

#### 4. 其他非报告类
- `新闻稿`
- `Press Release`
- `公告`
- `Announcement`
- `通函`
- `Circular`

### 排除规则

#### 1. 硬排除规则
文本中出现以下模式直接排除：
- `"补充" + "报告"`（任何顺序）
- `"业绩" + "预告"`
- `"演示" + "稿"`

#### 2. 软排除规则
出现以下关键词时触发人工检查：
- `"摘要"`
- `"Summary"`
- `"简版"`
- `"精简版"`

#### 3. 上下文排除
特定上下文中的关键词：
- `"报告"`前有`"补充"`或`"业绩"`
- `"Presentation"`出现在标题中
- `"Briefing"`与`"analyst"`同时出现

## 验证质量指标

### 1. 匹配置信度

#### 高置信度（>90%）
- 同时匹配公司全称和报告类型完整表述
- 来自官方域名的文件
- 文件大小在合理范围内

#### 中置信度（70%-90%）
- 匹配公司简称和报告类型关键词
- 来自权威财经网站
- 文件内容完整

#### 低置信度（<70%）
- 仅部分匹配关键词
- 来自未知来源
- 文件大小异常

### 2. 质量检查项

#### 文件完整性
- PDF结构完整，可正常打开
- 页面数合理（年报通常>50页，中报>20页）
- 文本可提取，非纯图片扫描

#### 内容合理性
- 包含财务报表典型结构（资产负债表、利润表等）
- 有审计师意见（年报）
- 包含公司基本信息

#### 版本正确性
- 年份与请求一致
- 报告类型与请求一致
- 公司信息与请求一致

## 特殊案例处理

### 1. 双语报告处理
- **中英文混合**：分别验证中英文部分
- **独立版本**：中文版和英文版分别处理
- **翻译版本**：以原文为准，翻译版作为参考

### 2. 合并报告处理
- **多年对比**：包含多年数据的报告按最新年份处理
- **合并报表**：集团公司合并报表按母公司验证
- **分部报告**：作为整体报告的一部分处理

### 3. 异常格式处理
- **扫描版PDF**：使用OCR预处理后验证
- **加密PDF**：尝试解密，否则标记为需要密码
- **损坏PDF**：尝试修复，否则重新下载

### 4. 边缘案例处理
- **公司更名**：验证时同时检查新旧名称
- **报告重发**：以最新版本为准
- **格式更新**：接受不同格式的正式报告

## 验证配置参数

### 1. 关键词配置
```yaml
validation:
  company_name:
    match_threshold: 0.7  # 公司名称匹配阈值
    variant_generation: true  # 是否生成变体
    language_support: ["zh", "en", "zh_Hant"]  # 支持语言
    
  report_type:
    annual_keywords: ["年报", "年度报告", "Annual Report", "年報", "全年業績"]
    interim_keywords: ["中报", "中期报告", "Interim Report", "中期業績", "半年度报告"]
    match_score_threshold: 2  # 匹配分数阈值
    
  exclusion:
    hard_exclude: ["补充报告", "业绩预告", "Presentation"]
    soft_exclude: ["摘要", "简版", "Summary"]
    context_exclude: true  # 是否启用上下文排除
```

### 2. 质量阈值
```yaml
quality:
  file_size:
    annual_min: 500000  # 年报最小500KB
    annual_max: 10000000  # 年报最大10MB
    interim_min: 200000  # 中报最小200KB
    interim_max: 5000000  # 中报最大5MB
    
  content:
    min_pages: 20  # 最小页数
    text_ratio: 0.3  # 文本比例阈值
    required_sections: ["公司名称", "报告期间"]  # 必须包含的章节
```

### 3. 验证模式
```yaml
mode:
  strict:  # 严格模式
    require_full_match: true
    check_exclusion_context: true
    verify_quality_metrics: true
    
  normal:  # 普通模式（默认）
    require_full_match: false
    check_exclusion_context: true
    verify_quality_metrics: false
    
  lenient:  # 宽松模式
    require_full_match: false
    check_exclusion_context: false
    verify_quality_metrics: false
```

## 验证结果输出

### 1. 验证报告格式
```json
{
  "file_path": "海尔智家_2024_年报.pdf",
  "validation_result": "PASS",
  "confidence_score": 0.95,
  "details": {
    "company_match": {
      "matched": true,
      "keywords_found": ["海尔智家", "海尔智家股份有限公司"],
      "score": 1.0
    },
    "report_type_match": {
      "matched": true,
      "keywords_found": ["年报", "年度报告"],
      "score": 0.9
    },
    "exclusion_check": {
      "matched": false,
      "keywords_found": []
    }
  },
  "quality_metrics": {
    "file_size": 721000,
    "page_count": 197,
    "text_ratio": 0.85
  },
  "metadata": {
    "download_source": "https://file.finance.qq.com/...",
    "download_time": "2026-02-22T15:37:00Z",
    "validation_time": "2026-02-22T15:38:00Z"
  }
}
```

### 2. 统计报告
- 总验证文件数
- 通过/失败/不确定数量
- 平均置信度分数
- 常见失败原因统计

### 3. 问题报告
- 失败文件列表及原因
- 需要人工检查的文件
- 建议的纠正措施

## 最佳实践

### 1. 验证前准备
- 确保PDF文件完整下载
- 检查文件基本属性（大小、创建时间）
- 备份原始文件

### 2. 验证过程
- 使用多语言关键词库
- 实施渐进式验证策略
- 记录详细的验证日志

### 3. 验证后处理
- 根据验证结果分类文件
- 生成结构化报告
- 清理临时文件

### 4. 持续优化
- 收集验证失败案例
- 更新关键词库
- 调整阈值参数

---

**版本**：1.0  
**更新日期**：2026年2月22日  
**基于经验**：海尔智家、安贤园中国财报验证实践  
**适用性**：A股、港股上市公司财务报告验证