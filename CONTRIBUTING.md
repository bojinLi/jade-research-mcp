# Contributing

Use Python 3.11+ on Windows (the validated environment uses Python 3.12). Install the locked dependencies and run `python -m pytest -q`.

GUI changes should be checked on an installed JADE instance using copied or synthetic inputs. Distinguish a dispatched action from a completed analysis; record the actual result, output file and limitations. Preserve observation-token, process-identity, bounds and modal-dialog checks.

Do not commit JADE executables, installers, license files, database contents, vendor examples, personal configuration, screenshots containing private information or confidential research data. Use generated, anonymous examples for reproducible issues and tests. Keep raw acceptance logs local.

For numerical changes, document the equation, units, assumptions and meaningful known-value checks. Do not represent heuristic CIF scores as probabilities or phase fractions, and do not label independent-engine results as JADE output.

Contributions are distributed under the repository's MIT license; third-party components retain their licenses.
