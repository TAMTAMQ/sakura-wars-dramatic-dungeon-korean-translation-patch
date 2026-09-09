# 이미지·영상 번역 자산

`index.json`은 선행 중국어 패치가 실제로 바꾼 이미지 및 컷신 컨테이너 후보를
원본 NitroFS 경로와 해시로 고정한다. 중국어 결과 이미지는 제품 입력으로
복사하지 않으며, 일본어 원본을 추출한 뒤 한국어 대체 자산을 제작한다.

- `cinema/`, `icat/`, `lips/`: 이미지 추출→PNG 작업본→원래 팔레트/타일 형식
  재인코딩→동일 크기 또는 명시된 재배치 검증
  - NCGR+NCER 자산은 `*.cells.png` 연락 시트로 전체 모습을 확인하고,
    `*.cells/cell-NNN.png`의 투명 RGBA 프레임을 편집한다. NCER 좌표와 OAM
    1D 128K 타일 매핑은 변환기가 자동으로 적용한다.
  - LIPS는 중국어 패치가 수정한 7세트뿐 아니라, 한자 공용이라 중국어판에서
    그대로였던 `lips01_03_00`까지 포함해 한국어에 필요한 8세트를 관리한다.
- `cpk/faCpkData.cpk`: ITOC 내부 1,616개 항목을 분리했다. 선행 패치가 바꾼
  ID 1538·1544의 정지 이미지 2장만 PNG 편집 대상으로 삼고, 나머지 음성·
압축 자산은 보존한다.

## 편집 위치

- `work/visual-png`: 일본어 원본 확인본. 편집하지 않는다.
- `work/visual-png-prior-patch`: 중국어 패치 참고본. 편집하지 않는다.
- `assets/visual/edits/nitrofs`: 일반 이미지와 NCGR 셀의 한국어 편집본.
- `assets/visual/edits/cpk`: CPK 내부 이미지의 한국어 편집본.

`InitVisualEdit`은 일본어 원본 PNG에서 누락된 작업 파일만 복사한다. 이미 존재하는
한국어 편집본은 덮어쓰지 않는다. `ImportPng`과 `ImportCpkPng`은 위 편집 폴더만
읽어 바이너리 교체물을 만든다.

빌드는 색인에 명시된 `replacement_path`만 적용하며 `review_status`가 완료되지
않은 자산은 기본적으로 일본어 원본을 유지한다.
