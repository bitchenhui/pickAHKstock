#!/bin/zsh
# 安装 / 卸载本机的哨兵定时任务（macOS launchd）。
#
#   scripts/install_sentinel.sh              安装（已安装则更新）
#   scripts/install_sentinel.sh --uninstall  卸载
#
# 装了之后：交易时段每 30 分钟（:05 和 :35，9:05 到 15:35）跑一次
# scripts/run_sentinel.sh，触发器命中才推送。电脑要开着机；合盖休眠时不会跑，
# 唤醒后 launchd 会补跑错过的那一次。
#
# 为什么要单独一份「运行副本」（~/pickahkstock-runtime）：
# macOS 不允许定时任务拉起的进程读取「文稿」「桌面」「下载」里的文件——
# 能列出文件名，但一读内容就是 Operation not permitted。开发用的仓库放在
# ~/Documents 下，所以定时任务读不了它。运行副本放在主目录根部（不受这个限制），
# 是仓库的一个普通克隆，每次运行前快进到云端的最新提交。
#
# 它做的事和不做的事：
# - 会联网取行情、会往你的 iPhone 推通知（通过 ~/.config/pickahkstock/sentinel.env
#   里的 Bark key），会读写 ~/.cache/pickahkstock/sentinel_state.json（去重状态）
# - 运行副本只做 git pull --ff-only，不改任何仓库文件，不提交，不下单
# - 不碰 ~/Documents 下的开发仓库
set -eu
label="com.pickahkstock.sentinel"
domain="gui/$(id -u)"
plist="$HOME/Library/LaunchAgents/$label.plist"
shim="$HOME/.config/pickahkstock/launch-sentinel.sh"
runtime="$HOME/pickahkstock-runtime"
here="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$domain/$label" 2>/dev/null || true
  rm -f "$plist" "$shim"
  echo "已卸载哨兵定时任务（$plist）。"
  echo "运行副本 $runtime 没有动；不需要的话手动删除：rm -rf $runtime"
  exit 0
fi

if [ ! -f "$HOME/.config/pickahkstock/sentinel.env" ]; then
  echo "缺少 $HOME/.config/pickahkstock/sentinel.env"
  echo "先建好它并写入 SENTINEL_BARK_KEY=... （只给本机用户可读：chmod 600），再运行本脚本。"
  exit 1
fi
chmod 700 "$HOME/.config/pickahkstock"
chmod 600 "$HOME/.config/pickahkstock/sentinel.env"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

origin="$(git -C "$here" remote get-url origin)"

# 1) 运行副本：没有就克隆，有就快进到云端最新提交
if [ ! -d "$runtime/.git" ]; then
  echo "创建运行副本 $runtime"
  git clone --quiet "$origin" "$runtime"
fi
git -C "$runtime" pull --ff-only --quiet origin master || \
  echo "（运行副本暂时拉不到最新提交，先用现有的）"

# 2) 运行副本自己的依赖环境
if [ ! -x "$runtime/.venv/bin/python" ]; then
  echo "为运行副本安装依赖（一次性，约一两分钟）"
  /usr/bin/python3 -m venv "$runtime/.venv"
  "$runtime/.venv/bin/python" -m pip install --quiet --upgrade pip
fi
"$runtime/.venv/bin/python" -m pip install --quiet -r "$runtime/requirements.txt"

# 3) launchd 调用的入口：同步运行副本，再跑仓库里的 run_sentinel.sh
cat > "$shim" <<EOF
#!/bin/zsh
# 由 scripts/install_sentinel.sh 生成，请勿手改；卸载用同一个脚本的 --uninstall。
export SENTINEL_GIT_PULL=1
exec /bin/zsh "$runtime/scripts/run_sentinel.sh"
EOF
chmod 700 "$shim"

# 4) 定时表：9:05 到 15:35，每个整点的 :05 和 :35
entries=""
for hour in 9 10 11 12 13 14 15; do
  for minute in 5 35; do
    entries="$entries    <dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>
"
  done
done

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$shim</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
$entries  </array>
  <key>RunAtLoad</key><false/>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF

launchctl bootout "$domain/$label" 2>/dev/null || true
launchctl bootstrap "$domain" "$plist"
echo "已安装哨兵定时任务：$plist"
echo "  运行副本：$runtime（每次运行前快进到云端最新提交）"
echo "  调度：交易时段每 30 分钟一次（9:05–15:35，:05 和 :35）"
echo "  日志：~/Library/Logs/pickahkstock-sentinel.log"
echo "  立刻试跑一次：launchctl kickstart -k $domain/$label"
echo "  卸载：        $0 --uninstall"
