# AndroZoo CSV 筛选与 APK 下载

[English](README.md) | 中文

项目包含三个独立任务：

1. 按 `vt_scan_date` 的年份以及 `vt_detection` 数量筛选 `latest.csv`。
2. 一次扫描并按年份分别输出良性和恶意样本 CSV。
3. 从筛选结果读取 `sha256`，使用多进程下载 APK，并记录每个任务的结果。

## 安装

```powershell
python -m pip install -r requirements.txt
```

项目已提供 `config.json`。`config.example.json` 是用于恢复的示例配置：

```powershell
Copy-Item config.example.json config.json
```

## 配置

```json
{
  "filter": {
    "input_csv": "data/latest.csv",
    "output_csv": "",
    "year": 2024,
    "min_vt_detection": 6,
    "max_vt_detection": null
  },
  "yearly_filter": {
    "input_csv": "data/latest.csv",
    "output_dir": "filtered/yearly_2018_2025",
    "start_year": 2018,
    "end_year": 2025,
    "benign_vt_detection": 0,
    "malware_min_vt_detection": 5
  },
  "download": {
    "input_csv": "",
    "download_dir": "downloads/apks",
    "result_csv": "downloads/download_results.csv",
    "workers": 4,
    "max_downloads": 0,
    "random_seed": 42,
    "resume": true,
    "api_key": "填写你的 AndroZoo API Key",
    "timeout_seconds": 120,
    "retries": 2,
    "chunk_size": 1048576,
    "proxy": {
      "enabled": false,
      "url": "http://127.0.0.1:7897"
    }
  }
}
```

示例使用相对于项目目录的路径：

- `data/latest.csv` 对应 `<项目目录>/data/latest.csv`。
- `downloads/apks` 对应 `<项目目录>/downloads/apks`。
- `downloads/download_results.csv` 对应 `<项目目录>/downloads/download_results.csv`。

也可以使用绝对路径。JSON 中的 Windows 反斜杠需要写成两个，例如
`D:\\data\\latest.csv`；也可以使用 `D:/data/latest.csv`。

### `filter`：筛选配置

| 字段 | 是否必填 | 含义 |
| --- | --- | --- |
| `input_csv` | 是 | AndroZoo 最新版 `latest.csv` 的位置。CSV 必须包含 `vt_scan_date` 和 `vt_detection`。 |
| `output_csv` | 否 | 筛选结果位置。空字符串表示按条件自动命名并保存在项目目录。 |
| `year` | 是 | `vt_scan_date` 的目标年份，例如 `2024`。这里不读取 `dex_date`。 |
| `min_vt_detection` | 是 | `vt_detection` 下限，包含该值。 |
| `max_vt_detection` | 否 | `vt_detection` 上限，包含该值；`null` 表示不限制。 |

所有筛选条件必须同时满足：

```text
vt_scan_date 的年份 == year
并且 vt_detection >= min_vt_detection
并且 vt_detection <= max_vt_detection（max 不为 null 时）
```

例如，`year=2024`、`min=6`、`max=null` 会自动生成：

```text
filtered_vt_scan_2024_detection_ge_6.csv
```

如需筛选 `vt_detection` 恰好等于 6，可设置：

```json
"min_vt_detection": 6,
"max_vt_detection": 6
```

无法解析的日期或检测数会被忽略，并计入程序最后显示的无效数据数量。筛选过程会通过
`tqdm` 显示百分比、速度、预计剩余时间、已处理行数和命中数。

### 为什么使用 `vt_scan_date` 作为时间

本项目按照 TIF 论文的数据构造方法使用 `vt_scan_date`。这里的年份表示 APK 被安全检测平台
观察到的近似时间，不表示 APK 的真实编译年份或应用商店发布日期。

论文研究恶意软件随时间演化造成的分布漂移，因此需要按照现实中的时间顺序组织数据：使用过去
样本训练，并使用之后观察到的样本测试。作者将这个时间称为 **application observation date**，
并用它划分时间环境。

没有使用其他日期字段的原因如下：

- `dex_date` 是 APK 压缩包内 `classes.dex` 文件附带的时间，可以被修改，也可能由打包工具生成。
  AndroZoo 官方说明该字段经常不可靠，很多 APK 的日期甚至是 1980 年。
- `added` 只是 APK 被加入 AndroZoo 的日期。APK 可能已经存在多年后才被 AndroZoo 收录，因此它
  也不等于 APK 的首次出现或发布日期。
- VirusTotal 的 `first submission date` 更接近样本首次被安全平台观察到的时间，但逐个通过 API
  查询整个数据集受到访问限制。

TIF 作者抽取了 10% 的较新样本，将 VirusTotal 的 `first submission date` 与 AndroZoo 的
`vt_scan_date` 比较，发现两者非常接近。因此作者在完整数据集上使用 `vt_scan_date` 近似
application observation date。详细说明见论文
[Appendix A.2: Timestamp selection](https://arxiv.org/html/2502.05098v1)；字段定义见
[AndroZoo 官方文档](https://androzoo.uni.lu/api_doc)。

因此，下面的配置：

```json
"year": 2024
```

表示选择 `vt_scan_date` 位于 2024 年的 APK，即近似选择在 2024 年被 VirusTotal 观察或扫描的
样本，而不是选择在 2024 年编译或发布的 APK。

另外，论文将 `vt_detection > 4` 的样本视为恶意软件。若严格复现该阈值，应配置：

```json
"min_vt_detection": 5,
"max_vt_detection": null
```

示例中的 `min_vt_detection=6` 是更严格的筛选条件，并不是论文的原始阈值。

### `yearly_filter`：按年份拆分良性和恶意样本

| 字段 | 是否必填 | 含义 |
| --- | --- | --- |
| `input_csv` | 是 | AndroZoo `latest.csv` 的位置。 |
| `output_dir` | 是 | 每年分类 CSV 和数量汇总文件的输出目录。 |
| `start_year` | 是 | 起始年份，包含该年份。 |
| `end_year` | 是 | 结束年份，包含该年份。 |
| `benign_vt_detection` | 是 | 良性样本必须等于的 `vt_detection`，本任务配置为 0。 |
| `malware_min_vt_detection` | 是 | 恶意样本的 `vt_detection` 最小值，5 表示论文条件 `vt_detection > 4`。 |

运行：

```powershell
python filter_yearly.py --config config.json
```

默认配置只扫描一次 `latest.csv`，按照 `vt_scan_date` 输出 2018–2025 年的数据。每年生成两个文件：

```text
filtered/yearly_2018_2025/
├── benign_2018_vt_detection_eq_0.csv
├── malware_2018_vt_detection_gt_4.csv
├── ...
├── benign_2025_vt_detection_eq_0.csv
├── malware_2025_vt_detection_gt_4.csv
└── yearly_counts.csv
```

分类规则：

- 良性：`vt_detection == 0`。
- 恶意：`vt_detection >= 5`，即 `vt_detection > 4`。
- `vt_detection` 为 1–4 的样本不属于以上两类，因此排除。
- 不在 2018–2025 范围内的样本排除。
- 每个输出 CSV 保留输入文件的原始表头和全部字段，即使某年没有匹配样本也会生成带表头的空文件。
- `yearly_counts.csv` 记录每年的良性数、恶意数和选中总数。

可以临时覆盖年份和路径：

```powershell
python filter_yearly.py --config config.json --start-year 2020 --end-year 2024
python filter_yearly.py --config config.json --input data/latest.csv --output-dir filtered/custom
```

### `download`：下载配置

| 字段 | 是否必填 | 含义 |
| --- | --- | --- |
| `input_csv` | 否 | 包含 `sha256` 的 CSV。留空时自动使用筛选输出文件。 |
| `download_dir` | 是 | APK 保存目录，不存在时自动创建。文件名为 `<sha256>.apk`。 |
| `result_csv` | 是 | 下载结果 CSV 的位置。 |
| `workers` | 是 | 并行下载进程数，最小值为 1，建议先使用 4。 |
| `max_downloads` | 否 | 本次最多下载的 APK 数量。`0` 或 `null` 表示不限制；正整数表示下载上限。 |
| `random_seed` | 否 | 随机抽样种子。相同输入、成功记录和种子会选出相同的一批 SHA256。 |
| `resume` | 是 | `true` 跳过结果中 `success=1` 的 SHA256；`false` 重新执行并覆盖原结果。 |
| `api_key` | 是 | AndroZoo API Key。为空时下载程序会退出。 |
| `timeout_seconds` | 否 | 单次下载的读取超时秒数，默认 120。 |
| `retries` | 否 | 失败后的重试次数。2 表示加上首次请求最多尝试 3 次。 |
| `chunk_size` | 否 | 每次写入的数据块字节数；1048576 等于 1 MiB。 |
| `proxy.enabled` | 是 | `true` 使用代理，`false` 直接连接。 |
| `proxy.url` | 启用代理时必填 | 代理地址，必须包含协议，例如 `http://127.0.0.1:7897`。 |

下载数量限制在排除已成功记录、重复 SHA256 和非法 SHA256 后生效。例如：

```json
"max_downloads": 100,
"random_seed": 42
```

- 如果剩余有效候选不超过 100 个，则全部下载。
- 如果剩余有效候选超过 100 个，则均匀随机选择 100 个下载。
- `resume=true` 时，以前已经成功的 APK 不占用本次的 100 个名额。
- 程序使用固定内存的蓄水池抽样，不会为了随机选择而把整个大型 CSV 加载到内存。
- 如需每次运行都可能选择不同样本，可以把 `random_seed` 设置为 `null`。

## 筛选 CSV

```powershell
python read_csv.py --config config.json
```

可以用命令行参数临时覆盖配置：

```powershell
python read_csv.py --config config.json --year 2023 --min-detection 5 --max-detection 20
```

输出保留原 CSV 的表头和全部字段，只筛选数据行。

## 多进程下载

先在 `config.json` 的 `download.api_key` 中填写 API Key，然后运行：

```powershell
python apk_download.py --config config.json
```

可以临时覆盖进程数、目录或代理：

```powershell
python apk_download.py --config config.json --workers 8 --download-dir downloads/apks
python apk_download.py --config config.json --proxy http://127.0.0.1:7897
python apk_download.py --config config.json --no-proxy
```

下载结果包含 `sha256`、绝对文件路径、`success`、状态、HTTP 状态码、文件大小、错误原因和
UTC 完成时间。`success=1` 表示成功，`success=0` 表示失败。

下载时会显示 `tqdm` 进度条，包括已完成数量、总任务数、下载速度、预计剩余时间，以及当前
成功和失败数量。`max_downloads` 为正整数时会显示确定的总任务数；不限制数量时显示累计完成数。

常见状态：

- `downloaded`：本次成功下载。
- `already_exists`：目标目录已有非空 APK，按成功处理。
- `failed`：请求或文件写入失败，原因见 `error`。
- `invalid_sha256`：SHA256 不是 64 位十六进制字符串。
- `worker_error`：下载子进程出现未预期错误。

下载先写入 `.part` 临时文件，完成后再改名为 `.apk`；失败不会留下不完整 APK。
