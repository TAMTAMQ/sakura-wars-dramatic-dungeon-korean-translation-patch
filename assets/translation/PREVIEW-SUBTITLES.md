# 차회 예고 음성 자막

사용자 지정: **3화뿐 아니라 모든 예고에, 위쪽 화면에 표시한다.**

제품 입력은 `preview-subtitles.json`, `preview-subtitles.asm` 및 기존
`assets/fonts/sources/NotoSerifKR-SemiBold.otf`이다. 추출 음원·Whisper 결과·저장 상태는
제품 빌드 입력이 아니다. 기존 예고 로고·제목 이미지는 유지한다.

| 예고 | CPK 영상 | ADX CPK ID | 음성 인덱스 | 자막 수 |
|---|---:|---:|---:|---:|
| 타이틀 대기 예고 | 1539 | 331 | 330 | 8 |
| 2화 예고 | 1540·1541 | 333 | 332 | 8 |
| 3화 예고 | 1542·1543 | 332 | 331 | 5 |
| 최종화 예고 | 1544 | 334 | 333 | 6 |

## 표시 및 복원

- 사용자 최종 선택은 **위 화면 하단 중앙**이다. y=144..175에 13px
  Noto Serif KR SemiBold로 최대 두 줄을 표시한다. 글자 영역을 가로·세로
  가운데 정렬하고, 아이보리색과 4단계 안티앨리어싱을 사용한다.
- 패널을 2bpp로 저장하고 실행 중 4bpp로 풀어 그린다. 64 KiB 예약 안에
  코드·27개 패널·복원 버퍼가 모두 들어가는지 빌드에서 검사한다.
- 실제 ADXT 샘플 수와 주파수가 시간 기준이다. 프레임 수로 시간을 재지 않는다.
- 음성 인덱스 330..333에서만 작동한다. 자막 사이의 공백 및 재생 종료 시
  BG0, VRAM, 팔레트, 혼합 및 화면 밝기 설정을 복원한다.
- main/3D가 아래, sub가 위인 예고 화면 배치와 VRAM C의 sub BG 매핑을
  확인한다. 다른 배치에서는 표시를 생략한다.
- 원래 예고는 위 화면을 MASTER_BRIGHT(`0x0400106C`)로 검게 만들므로
  자막 표시 중에만 밝기를 복구한다.

## 빌드

`tools/run-translation-pipeline.ps1 -Mode Build`에 통합했다.
기존 Python 환경의 Pillow·Capstone과 armips v0.11.0 Windows x86을 사용한다.
armips 기본 경로는 프로젝트 기준 `../../vendor/armips-v0.11.0/armips.exe`이며
`-Armips`로 변경할 수 있다. 실행 파일 SHA-256은 빌더가 확인한다.

```text
02c589ae509a7706bf73d0507949ff722288f1d2deea67a13f33c741bc8696ba
```

ARM946E-S ARMv5TE/Thumb 원천을 조립한 뒤 기계어를 해석·재조립하여
바이트 일치를 검사한다. 원본 해시, 훅의 예상 바이트, autoload/BSS 배치,
자막 겹침·폭·누락도 검사한다.

ARM9 autoload 항목을 추가해 `0x023D0000`에 자막 코드와 자료를 복사한다.
SDK Main arena 상한을 `0x023E0000`에서 `0x023D0000`으로 바꿔 64 KiB를
예약한다. 카드의 ARM9 시작 주소 `0x4000`은 유지하며 뒤쪽 ROM 자료와 FAT를
함께 이동한다. 모든 FAT 파일과 등록 범위 밖 바이트를 대조한다.

## 검증 범위 — 2026-11-04

- 주 빌드 전체 재생성 통과. 자막 삽입 후 FAT 파일 2,789개 동일.
- 최종 ROM SHA-256:
  `d795c7ce077192017415f7531847bf484e64bf0f20d022fc370864a9b5ebbf8b`
- 같은 최종 ROM의 DeSmuME libretro 실행에서 4개 예고를 재생했다.
  27개 자막 모두 위 화면의 실제 픽셀이 입력 자막 이미지와 일치한다.
- 예고별 종료 후 자막 비활성화를 확인했다. START 건너뛰기 입력부터
  제거까지 관측값은 2프레임이며 원래 게임의 음성 종료와 함께 지워진다.
- 자동 대기 예고는 원래 경로를 사용했다. 추가 예고는 원래 선택표의
  **자료 항목 세 곳**만 RAM에서 바꿔 호출했다. 기계어와 ROM 파일은 바꾸지
  않았다. 스토리 진행을 통한 장별 진입이나 실기 완주는 이번 검증 범위가 아니다.
- 근거: `work/preview-subtitles/runtime-verification.json`, 각 실행의
  `identity.json`·`interventions.json`·스크린샷, `verified-all-previews.png`.

`out/preview-subtitles/dramatic-dungeon-kr.xdelta`를 무수정 일본판에 재적용해
최종 ROM과 일치를 검사했다. 이 결과를 v0.4로 배포하며, 기존 v0.3의
배포 파일은 보존한다.
