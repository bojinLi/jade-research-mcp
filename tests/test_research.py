import json
from pathlib import Path

import numpy as np
import pytest

from jade_mcp import research as xr
from jade_mcp import server


@pytest.fixture(autouse=True)
def results(tmp_path,monkeypatch):
    monkeypatch.setattr(xr,"RESULTS",tmp_path/"results")


@pytest.fixture
def pattern(tmp_path):
    rng=np.random.default_rng(21)
    x=np.arange(20,61,0.01)
    y=40+0.2*(x-20)+xr.pseudo_voigt(x,1200,32.15,0.24,0.35)+xr.pseudo_voigt(x,800,44.4,0.36,0.6)+rng.normal(0,0.5,len(x))
    path=tmp_path/"synthetic.xy"
    np.savetxt(path,np.column_stack([x,y]))
    return path


def test_known_peaks_and_non_destructive_audit(pattern):
    before=xr.digest(pattern)
    result=xr.analyze(str(pattern),1.5406)
    assert result['peak_count']==2
    assert [p['two_theta_deg'] for p in result['peaks']]==pytest.approx([32.15,44.4],abs=0.02)
    assert xr.digest(pattern)==before
    saved=json.loads((Path(result['output_dir'])/'analysis.json').read_text(encoding='utf-8'))
    assert saved['input']['source_sha256']==before
    assert (Path(result['output_dir'])/'report.html').is_file()


def test_joint_peak_fit_recovers_widths_and_covariance(pattern):
    result=xr.fit_peaks(str(pattern),[32.13,44.42],half_window_deg=0.8,wavelength_angstrom=1.5406)
    assert [p['center_deg'] for p in result['peaks']]==pytest.approx([32.15,44.4],abs=0.002)
    assert [p['fwhm_deg'] for p in result['peaks']]==pytest.approx([0.24,0.36],rel=0.005)
    assert all(p['fwhm_esd_deg']>0 for p in result['peaks'])
    assert np.asarray(result['covariance']).shape==(10,10)
    assert result['rmse']<1


def test_scherrer_instrument_and_unit_conversion():
    angle,wave,size,k=40,1.5406,50,0.9
    beta=np.rad2deg(k*wave/(size*10*np.cos(np.deg2rad(angle/2))))
    observed=np.hypot(beta,0.08)
    assert xr.scherrer(angle,observed,wave,0.08)['coherent_domain_size_nm']==pytest.approx(size)
    with pytest.raises(ValueError): xr.scherrer(angle,0.07,wave,0.08)
    with pytest.raises(ValueError): xr.scherrer(0,0.2,wave)


def test_williamson_hall_known_size_and_strain():
    angles=np.array([30,45,60,80,100.])
    theta=np.deg2rad(angles/2)
    wave,size,strain,k=1.5406,60,0.0012,0.9
    beta=(k*wave/(size*10)+4*strain*np.sin(theta))/np.cos(theta)
    widths=np.hypot(np.rad2deg(beta),0.06)
    result=xr.williamson_hall(angles,widths,wave,[0.06]*5)
    assert result['domain_size_nm']==pytest.approx(size,rel=1e-8)
    assert result['microstrain']==pytest.approx(strain,rel=1e-8)


@pytest.mark.parametrize('system,cell,hkls',[
    ('cubic',[4.05],[[1,1,1],[2,0,0],[2,2,0],[3,1,1]]),
    ('hexagonal',[3.21,5.21],[[1,0,0],[0,0,2],[1,0,1],[1,0,2],[1,1,0]]),
    ('tetragonal',[3.8,5.1],[[1,0,0],[0,0,2],[1,0,1],[1,1,1]]),
    ('orthorhombic',[4,5,6],[[1,0,0],[0,1,0],[0,0,1],[1,1,1],[2,0,1]])])
def test_lattice_known_cells(system,cell,hkls):
    h,k,l=np.array(hkls).T
    if system=='cubic': inv=(h*h+k*k+l*l)/cell[0]**2
    elif system=='hexagonal': inv=4/3*(h*h+h*k+k*k)/cell[0]**2+l*l/cell[1]**2
    elif system=='tetragonal': inv=(h*h+k*k)/cell[0]**2+l*l/cell[1]**2
    else: inv=h*h/cell[0]**2+k*k/cell[1]**2+l*l/cell[2]**2
    angles=np.rad2deg(2*np.arcsin(1.5406*np.sqrt(inv)/2))
    result=xr.refine_lattice(angles,hkls,1.5406,system)
    assert list(result['cell_angstrom'].values())==pytest.approx(cell)


def test_reject_rank_deficient_indexing():
    with pytest.raises(ValueError,match='independent'):
        xr.refine_lattice([30,40,50],[[1,0,0],[2,0,0],[3,0,0]],1.5406,'hexagonal')


def test_conversions_roundtrip():
    result=server.xrd_convert([30,45,80],'two_theta_deg',1.5406)
    other=server.xrd_convert(result['q_inverse_angstrom'],'q_inverse_angstrom',1.5406)
    assert other['two_theta_deg']==pytest.approx([30,45,80])
    with pytest.raises(ValueError):server.xrd_convert([0.1],'d_angstrom',1.5406)


def test_mdi_export_roundtrip(pattern):
    result=server.xrd_export_xy(str(pattern),'mdi',1.5406)
    original=xr.read_pattern(str(pattern))
    restored=xr.read_pattern(result['output'])
    assert restored.x==pytest.approx(original.x,abs=1e-7)
    assert restored.y==pytest.approx(original.y,rel=1e-8)
    assert restored.metadata['wavelength_angstrom']==1.5406


@pytest.mark.parametrize('trailer',[' -1',' -1\n\x1a',' -1 0 0 0 0 0 0'])
def test_legacy_mdi_exact_terminator_is_not_intensity(tmp_path,trailer):
    source=tmp_path/'legacy.mdi'
    source.write_text('legacy\n10 1 1 CU 1.5406 14 5\n1 2 3 4 5'+trailer,encoding='ascii')
    p=xr.read_pattern(str(source))
    assert p.y.tolist()==[1,2,3,4,5]
    assert p.x.tolist()==[10,11,12,13,14]
    assert 'terminator' in p.metadata['notes'][0]


@pytest.mark.parametrize('trailer',[' 6 -1',' 0 0',' -1 -1',' -1 0 0 0 0 0 0 0 0'])
def test_mdi_unexplained_extra_data_remain_rejected(tmp_path,trailer):
    source=tmp_path/'extra.mdi'
    source.write_text('extra\n10 1 1 CU 1.5406 14 5\n1 2 3 4 5'+trailer,encoding='ascii')
    with pytest.raises(ValueError,match='mismatch'):xr.read_pattern(str(source))


@pytest.mark.parametrize('content',[
    '10 1\n11 2\n11 3\n12 4\n13 5',
    '10 1\n11 2\nBAD ROW\n12 3\n13 4\n14 5',
    '0 1\n1 2\n2 3\n3 4\n4 5',
    'title\n5 .02 1 CU 1.54 6 51\n1 2 3 4 5'])
def test_reject_corrupt_or_ambiguous_data(tmp_path,content):
    path=tmp_path/('bad.mdi' if content.startswith('title') else 'bad.xy')
    path.write_text(content,encoding='utf-8')
    with pytest.raises(ValueError): xr.read_pattern(str(path))


def test_binary_is_not_misread_as_text(tmp_path):
    path=tmp_path/'data.dat'
    path.write_bytes(b'RAW\x00'+b'1234'*10)
    with pytest.raises(ValueError,match='Binary'):xr.read_pattern(str(path))


def test_nonuniform_als_is_rejected(tmp_path):
    path=tmp_path/'irregular.xy'
    np.savetxt(path,[[10,1],[11,2],[12,3],[14,2],[15,1]])
    with pytest.raises(ValueError,match='evenly'):xr.preprocess(xr.read_pattern(str(path)))


def test_local_cif_diffraction_known_silicon(tmp_path):
    from pymatgen.core import Lattice,Structure
    structure=Structure.from_spacegroup('Fd-3m',Lattice.cubic(5.431),['Si'],[[0,0,0]])
    source=tmp_path/'silicon.cif'
    structure.to(filename=source)
    result=xr.simulate_cif(str(source),1.5406,20,90)
    assert result['peaks'][0]['two_theta_deg']==pytest.approx(28.44,abs=0.03)
    x=np.arange(20,90,0.02)
    y=np.full_like(x,10.)
    for peak in result['peaks']:
        y+=xr.pseudo_voigt(x,peak['relative_intensity'],peak['two_theta_deg'],0.15,0.4)
    pattern=tmp_path/'silicon.xy'
    np.savetxt(pattern,np.column_stack([x,y]))
    matches=xr.match_cifs(str(pattern),[str(source)],1.5406)
    assert matches['candidates'][0]['score']>0.99
    assert len(matches['candidates'][0]['matches'])>=4


def test_batch_preserves_failures(pattern):
    result=server.xrd_batch_analyze([str(pattern),str(pattern)+'.missing'],1.5406)
    assert result['succeeded']==1
    assert result['files'][1]['ok'] is False


def test_saved_result_directory_boundary(tmp_path):
    outside=tmp_path/'outside.json'
    outside.write_text('{}')
    with pytest.raises(ValueError):server.xrd_read_result(str(outside))
