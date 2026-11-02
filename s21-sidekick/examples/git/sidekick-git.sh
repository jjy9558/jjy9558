# 커밋 관중석 — bash/zsh용. ~/.bashrc 등에서:  source /path/to/s21-sidekick/examples/git/sidekick-git.sh
# pre-push 훅은 push가 '성공했는지' 모르기 때문에, git push를 감싸는 함수로 결과를 본다.
# SIDEKICK_DIR: 이 저장소 경로 (python -m sidekick 이 import 되도록)
SIDEKICK_DIR="${SIDEKICK_DIR:-$HOME/s21-sidekick}"
_sidekick() { PYTHONPATH="$SIDEKICK_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m sidekick notify "$@" >/dev/null || true; }

# gpush: git push 후 성공이면 환호, 실패면 야유. git의 종료 코드는 그대로 돌려준다.
gpush() {
  git push "$@"; local rc=$?
  local branch; branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [ $rc -eq 0 ]; then _sidekick push_ok --detail "$branch"; else _sidekick push_fail --detail "$branch"; fi
  return $rc
}

# gci: 방금 올린 커밋의 GitHub Actions 결과를 기다렸다가 알림 (gh CLI 필요)
gci() {
  local id; id="$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')" || return 1
  if gh run watch "$id" --exit-status >/dev/null; then _sidekick ci_ok; else _sidekick ci_fail; return 1; fi
}

# gmerge <PR번호> [gh pr merge 옵션...]: 머지 성공 시 불꽃 (변경 줄 수가 big_pr_lines 미만이면 박수)
gmerge() {
  local pr="$1"; shift
  local lines; lines="$(gh pr view "$pr" --json additions,deletions -q '.additions + .deletions' 2>/dev/null || echo 0)"
  gh pr merge "$pr" "$@" && _sidekick pr_merged --detail "PR $pr" --lines "${lines:-0}"
}

# git 별칭으로 쓰고 싶다면 (함수 대신):
#   git config --global alias.pushc '!f() { git push "$@" && python3 -m sidekick notify push_ok || { python3 -m sidekick notify push_fail; exit 1; }; }; f'
