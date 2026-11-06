"""Create clearly synthetic, anonymous examples; no experimental/private data."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

import numpy as np
from pymatgen.core import Structure,Lattice
from jade_mcp.research import pseudo_voigt

folder=ROOT/'examples'
folder.mkdir(exist_ok=True)
x=np.arange(10,90.0001,.02)
rng=np.random.default_rng(20261105)
y=40+.12*x+pseudo_voigt(x,1200,32.15,.24,.35)+pseudo_voigt(x,800,44.4,.36,.6)+rng.normal(0,.5,len(x))
np.savetxt(folder/'synthetic_two_peaks.xy',np.column_stack([x,y]),fmt='%.8f',header='SYNTHETIC test data: two_theta_deg intensity')
header=['SYNTHETIC two peaks; no experimental meaning',f'{x[0]:.8f} .02 1.0 CU 1.5406 {x[-1]:.8f} {len(x)}']
header.extend(' '.join(f'{v:.6f}' for v in y[i:i+8]) for i in range(0,len(y),8))
(folder/'synthetic_two_peaks.mdi').write_text('\n'.join(header)+'\n',encoding='ascii')
# These deliberately rounded cells demonstrate a workflow; they are not reference values for measured samples.
Structure.from_spacegroup('P6_3/mmc',Lattice.hexagonal(3.2,5.2),['Mg'],[[1/3,2/3,1/4]]).to(filename=folder/'illustrative_Mg.cif')
Structure.from_spacegroup('Fm-3m',Lattice.cubic(4.2),['Mg','O'],[[0,0,0],[.5,.5,.5]]).to(filename=folder/'illustrative_MgO.cif')
(folder/'README.txt').write_text('全部为程序生成的测试样例，不是实验数据。\n两峰谱：峰位 32.15°、44.40°；FWHM 0.24°、0.36°；伪Voigt混合系数0.35、0.60。\nCIF使用人为取整晶格参数：Mg a=3.2 Å, c=5.2 Å；MgO a=4.2 Å，仅用于演示。\n不要把测试谱或示范晶胞当成实验结果或标准卡片。\n',encoding='utf-8')
print(folder)
