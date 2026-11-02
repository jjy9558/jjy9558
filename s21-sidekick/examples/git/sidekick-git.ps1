# 커밋 관중석 — PowerShell용. $PROFILE 에서:  . C:\dev\s21-sidekick\examples\git\sidekick-git.ps1
# pre-push 훅은 push 성공 여부를 모르므로, git push를 감싸는 함수로 결과를 본다.
if (-not $env:SIDEKICK_DIR) { $env:SIDEKICK_DIR = "C:\dev\s21-sidekick" }

function Invoke-Sidekick {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
  $old = $env:PYTHONPATH
  $env:PYTHONPATH = $env:SIDEKICK_DIR
  try { python -m sidekick notify @Rest *> $null } catch { } finally { $env:PYTHONPATH = $old }
}

# gpush: git push 후 성공이면 환호, 실패면 야유. 종료 코드는 git 것을 유지.
function gpush {
  git push @args
  $rc = $LASTEXITCODE
  $branch = git rev-parse --abbrev-ref HEAD 2>$null
  if ($rc -eq 0) { Invoke-Sidekick push_ok --detail "$branch" } else { Invoke-Sidekick push_fail --detail "$branch" }
  $global:LASTEXITCODE = $rc
}

# gci: 최근 GitHub Actions 실행을 기다렸다가 결과 알림 (gh CLI 필요)
function gci {
  $id = gh run list --limit 1 --json databaseId -q '.[0].databaseId'
  gh run watch $id --exit-status *> $null
  if ($LASTEXITCODE -eq 0) { Invoke-Sidekick ci_ok } else { Invoke-Sidekick ci_fail }
}

# gmerge 12 --squash : 머지 성공 시 불꽃 (작은 PR은 박수)
function gmerge {
  param([Parameter(Mandatory = $true)][string]$Pr, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
  $lines = gh pr view $Pr --json additions,deletions -q '.additions + .deletions'
  gh pr merge $Pr @Rest
  if ($LASTEXITCODE -eq 0) { Invoke-Sidekick pr_merged --detail "PR $Pr" --lines "$lines" }
}
