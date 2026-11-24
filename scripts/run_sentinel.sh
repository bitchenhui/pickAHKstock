#!/bin/zsh
# 哨兵的一次运行。交易时段由本机 launchd 每 30 分钟调用一次，也可以手动跑：
#   scripts/run_sentinel.sh
#
# 为什么在本机跑：GitHub Actions 的定时触发会把排队中的多次触发合并成一次，
# 实测每个工作日只在收盘后（北京时间 16:20–17:40）跑，做不到盘中提醒。
#
# 推送用的 Bark key 放在 ~/.config/pickahkstock/sentinel.env（不进仓库）：
#   SENTINEL_BARK_KEY=xxxx
# 休市或还没开盘时没有新行情，哨兵自己会跳过。
#
# SENTINEL_GIT_PULL=1 时，运行前先把本仓库快进到云端的最新提交——
# 专供定时任务用的运行副本（见 install_sentinel.sh）：云端每天刷新卡片数据并提交，
# 哨兵读的催化剂、相对强度、盯价都来自这些提交。只做快进，拉不下来就照常跑。
set -u
cd "$(dirname "$0")/.."
if [ "${SENTINEL_GIT_PULL:-0}" = "1" ]; then
  git pull --ff-only --quiet origin master >/dev/null 2>&1 || true
fi
if [ -f "$HOME/.config/pickahkstock/sentinel.env" ]; then
  set -a
  source "$HOME/.config/pickahkstock/sentinel.env"
  set +a
fi
mkdir -p "$HOME/Library/Logs"
log="$HOME/Library/Logs/pickahkstock-sentinel.log"
export PYTHONPYCACHEPREFIX=/tmp/pickahk-pycache
revision="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 哨兵开始（$revision）=====" >> "$log"
.venv/bin/python -m research.cli sentinel >> "$log" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "哨兵运行失败（退出码 $rc）" >> "$log"
fi
exit 0    # 失败也返回 0：单次失败不该让 launchd 反复重跑
