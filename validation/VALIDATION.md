# Validation summary

Validated on Windows, MDI JADE 9 Chinese, Python 3.12. This is software-workflow acceptance on one installation, not metrological certification or a guarantee for other installations.

## Automated verification

- 35 passing tests: known peak centers/widths, covariance, Scherrer units and instrument correction, known Williamson–Hall size/strain, four lattice systems, Bragg conversion and MDI roundtrips, legacy terminators, CIF simulation/matching, invalid-data rejection, batch failures, path boundaries and GUI guards.
- All 32 MCP tools have successful stdio invocation records across supervised acceptance sessions.
- Independent-engine protocol acceptance: 13 assertions, 24 tool calls including expected errors. Repeated after installing the package in a separate environment.
- Original inputs were checked by SHA-256. GUI acceptance used copies of installed demonstration files.

## Supervised JADE workflows

| Workflow | Observed result |
|---|---|
| Native file open | RD and MDI loaded by full path; state, exported data and reports inspected |
| Native MDI roundtrip | 2001 points; intensity difference 0; angular difference approximately 1.42e-14 degrees |
| Background/peak search | Background processing and peak-search results observed |
| Corundum search/match | Expected Corundum (Al2O3) hit in the installation's MDI-500 library |
| Single-phase WPF | Corundum example converged; JADE R=10.32%, E=8.94%, R/E=1.15 |
| Three-phase WPF | CPD-3 example converged; JADE R=9.64%, E=8.67%, R/E=1.11; native report saved |
| Cell refinement | Fluorite example, cubic F lattice; a=5.461481 Å, formal esd=0.000578 Å |
| Native macro batch | Same saved smoothing/background/export macro applied to two selected RD copies; both produced 7250-point outputs |
| Lifecycle | Launch via desktop shell without an open Explorer folder; JADE survived a short MCP client's disconnect; minimized main window restored |

JADE's R field is reported as named by JADE. These values are example-specific and do not establish an expected threshold for arbitrary samples. Whole-range automatic profile fitting in the Corundum example returned R=28.38% and weak-peak uncertainty flags; its scientific fitness was not accepted merely because the operation completed.

## Limits

- No certified composition was provided: absolute phase/amorphous fractions and detection limits were not certified.
- No known multi-tilt stress sequence was provided: residual-stress controls were inspected, but stress accuracy was not validated. The independent engine does not implement stress analysis.
- Real instrument angle/width calibration and unknown-phase indexing success rates were not established.
- Other JADE versions/languages, instruments' unprovided binary variants, locked desktops and remote sessions were not validated.
- The independent parser rejects inconsistent original headers rather than guessing or silently dropping intensities. Native JADE read/export is the alternative for such files.

## Reproduction and evidence privacy

Run `python -m pytest -q` in the installed project environment. Run `scripts/acceptance_mcp.py` for independent-engine stdio acceptance; optionally use `--installed` after installing the package into a separate environment. Local evidence is written under `validation/full/` and is ignored by Git.

For GUI reproduction, copy the vendor demonstration files from a licensed installation to a scratch folder; observe current controls and live menus before every action; verify reports and output data rather than successful click dispatch. Never reuse handles from someone else's acceptance log.

Raw logs, screenshots, personal configuration, local paths, licensed databases and vendor samples are deliberately absent from this repository.
