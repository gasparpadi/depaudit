# depaudit

**清单里写了什么不重要，代码里 import 了什么才重要。**

`depaudit` 扫描项目里的 Python 文件，按**真实的 import 语句**核对依赖清单，
指出哪些包声明了却没人用、哪些模块用了却没声明、哪些可以用标准库替代。

- 零依赖，Python 3.10+ 标准库即可运行
- 单文件，`depaudit.py` 拷走就能用
- 只读项目目录，不改任何文件、不装任何东西

## 为什么需要它

接手别人的项目，第一件事往往是看依赖清单。然后你会发现清单和代码对不上：

```
requirements.txt 里 14 个包，代码里实际 import 了 1 个。
```

剩下那 13 个是谁加的、干什么用的，注释里也没写。
清单只能证明「有人声明过」，不能证明「有人在用」——这两件事差得很远。

`depaudit` 的判断依据是代码本身：

| 报告项 | 含义 | 该怎么处理 |
|---|---|---|
| **未使用** | 清单里声明了，代码里找不到 import | 确认构建脚本/测试夹具里也没用，然后删掉 |
| **未声明** | 代码 import 了，清单里没有 | 补进清单，否则别人装不上 |
| **可替代** | 某个包能用标准库替掉 | 看代价再决定 |
| **动态导入** | `importlib.import_module` 之类 | 无法静态判定，不计入「未使用」 |

## 用法

```bash
python depaudit.py --project .

# 指定清单文件
python depaudit.py --project . --manifest requirements.txt

# 机器可读输出
python depaudit.py --project . --json
```

输出：

```
项目：D:\Workspace\myproject
清单：requirements.txt
声明依赖 14 个，代码实际 import 6 个模块

清单里有 13 个包在代码中找不到 import：
  - requests                requests==2.31.0
    代码里没有找到对应的 import
  - pandas                 pandas>=2.0.0
    代码里没有找到对应的 import
  ...

代码 import 了 1 个未在清单中声明的模块：
  - numpy                   出现在 src/processing.py

可以用标准库替代的依赖（1 个）：
  - requests → urllib.request
    代价：失去会话保持、连接池与第三方认证的便利封装

说明：删掉一个包前请确认——
  1. 它可能只在构建脚本或测试夹具里被用到；
  2. 它可能通过动态导入被加载；
  3. 移除它可能改变运行时行为。
减依赖本身不是目的，少装一个包省下来的时间常常抵不上排查成本。
```

## 「可替代」这一项的规矩

列出替代方案时，**必须同时说明替代后失去什么能力**。
只说「可以用 `urllib` 代替 `requests`」是不负责任的——

| 依赖 | 标准库替代 | 代价 |
|---|---|---|
| `requests` | `urllib.request` | 失去会话保持、连接池与第三方认证的便利封装 |
| `pyyaml` | `json` | 失去注释、多文档与自定义标签支持 |
| `numpy` | `array` / `statistics` | 失去向量化运算与多维数组能力，性能显著下降 |
| `click` | `argparse` | 失去命令分组与装饰器式声明 |
| `dateutil` | `datetime` | 失去自然语言日期解析与时区规则支持 |

内置的替代表是有限的（十几个常见包）。表里没有的包不会被凭空推荐替代品——
这比给一个错的建议要好。

## 已知局限

- **只扫 `.py` 文件**。C 扩展、Rust/Go 子模块、前端构建脚本的依赖它看不到。
- **静态分析有边界**。通过 `getattr` 动态取模块、通过字符串拼接的 import、插件注册机制都无法静态识别。
  动态导入会被单独列出来，但插件注册那一类仍然可能误报。
- **不解析 `pyproject.toml`**。目前只支持 `requirements.txt` 风格的清单；
  找到 `pyproject.toml` 时会显示文件名但不解析其依赖数组。
- **不做传递依赖分析**。`requests` 装进来会带 `urllib3`、`certifi` 等，这些不在你的清单里，也不会被报告。
- **不判断版本约束是否合理**。`numpy>=1.0` 会不会和 `numpy>=2.0` 冲突，它不管。
- **替代表是硬编码的**，不会联网查询最新情况。

## 跑测试

```bash
python test_depaudit.py
```

测试用独立的临时项目目录，不读你当前的项目。

## 为什么还要做这个

`pipdeptree`、`pip-audit` 这些工具查的是**已安装环境**里的依赖关系，
`pip list` 查的是**装了什么**。它们都不回答那个真正的问题：
*这个项目实际需要什么？*

`depaudit` 从代码出发往回看。方向反了，结论就反过来了——
装了一堆用不上的包，和声明了一堆用不上的包，都是同一类浪费。

## 来源与参考

- Python Packaging User Guide：<https://packaging.python.org/en/latest/>
- PEP 508（依赖声明格式）：<https://peps.python.org/pep-0508/>
- Python `ast` 模块文档：<https://docs.python.org/3/library/ast.html>

---

## 关于作者

gasparpadi 做依赖清单减法。这个工具先看代码实际 import 了什么，再谈清单该留什么。

- 个人主页：<https://gasparpadi.github.io>
- 仓库：<https://github.com/gasparpadi/depaudit>

## 许可

MIT，见 [LICENSE](LICENSE)。
