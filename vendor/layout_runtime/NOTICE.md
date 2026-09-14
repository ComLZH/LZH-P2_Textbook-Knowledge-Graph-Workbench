# P2 离线布局运行时

本目录用于教师端无网络、无开发者 PATH 的知识图谱首次布局。

- `node/node.exe`：Node.js v22.16.0，Windows x64，MIT 许可证及第三方许可见 `node/LICENSE.txt`。
- `elk/elk.bundled.js`：elkjs 0.12.0，许可证为 `EPL-2.0 OR GPL-3.0-or-later`，本项目按 EPL-2.0 选项使用；原始许可见 `elk/LICENSE.md`。
- `elk_layout_runner.mjs`：P2 本地适配器，只读取临时 JSON 并写回布局结果，不访问网络。

固定文件摘要（SHA-256）：

- `node/node.exe`：`C5FF4C736112DD483C750FD4149D30C8A116DB1A49B8B3EC88BE4B65E6C86C19`
- `elk/elk.bundled.js`：`1222E44F953CE7746AF23801E723708F8E6F436B8B377A6A5FC7552F34A307B3`
- npm 原始 `elkjs-0.12.0.tgz`：`C1D7719723E020B10724E3CCBC935696A2884F1E78D361CDD03766309E8E8E2A`

运行时是可替换布局后端，不承载业务事实。缺失、损坏、超时或输出非法时，应用回退到内置 Python/NetworkX 布局并显示诊断，不要求教师安装 Node.js。
