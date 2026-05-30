comfy-bridge 分发包 —— 让 ComfyUI 的 OpenAI/Anthropic/Gemini/Tripo/ByteDance·Seedance
节点改走你自己的 key 或 LLM 网关，绕开 comfy.org 计费。目标机【无需安装 Python】。

== 用法（3 步）==
1) 配置：把 .env.example 复制为 .env，填入你的网关 BASE_URL 和 API_KEY
   （只填要用的厂商；ByteDance/Seedance 填 BYTEPLUS_BASE_URL / BYTEPLUS_API_KEY）。

2) 起 bridge：双击 comfy-bridge.exe（会弹个窗口打印日志，保持开着）。
   默认监听 127.0.0.1:8190。

3) 接 ComfyUI：
   a. 把 comfy-bridge-gating 整个文件夹拷到  <你的ComfyUI>\custom_nodes\  下；
   b. 启动 ComfyUI 时加参数：  --comfy-api-base=http://127.0.0.1:8190
   c. 重启 ComfyUI。菜单里的 api 节点即走你的网关。

== 验证 ==
浏览器打开  http://127.0.0.1:8190/comfy-bridge/gating  应返回一段 JSON。

== 注意 ==
- .env 含密钥，别外传；exe 仅绑 127.0.0.1，勿暴露公网。
- 升级：用新版 exe 替换即可（配置在 .env，不受影响）。
- 改了 .env 要重启 comfy-bridge.exe；改了节点白名单要重启 ComfyUI。
- 这个 exe 只是 bridge 服务；comfy-bridge-gating 必须是 .py 放进 ComfyUI（在 ComfyUI 的
  Python 里运行）；ComfyUI 本身仍需它自己的 Python+torch 环境。
