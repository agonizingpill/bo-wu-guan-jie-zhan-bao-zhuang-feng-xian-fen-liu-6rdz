import {field, stamp} from '../components.js';
import {materials} from '../constants.js';
import {dateTime, escape as e} from '../format.js';

const materialNames = Object.keys(materials);
const ratio = (value) => Number(value).toFixed(3);

function metricTable(metrics, thresholds) {
  const rows = materialNames.map((material) => {
    const bucket = metrics.materials[material];
    return `<tr><td>${e(materials[material])}</td><td class="mono">${ratio(thresholds[material])}</td>
      <td class="mono">${bucket.unpacked}</td><td class="mono">${bucket.misses}</td>
      <td class="mono">${bucket.positives}</td></tr>`;
  }).join('');
  return `<table class="metric-table"><thead><tr><th>材质</th><th>生效阈值</th><th>开箱数</th><th>漏检</th><th>高风险样本</th></tr></thead>
    <tbody>${rows}</tbody><tfoot><tr><td>合计</td><td class="mono">—</td><td class="mono">${metrics.total.unpacked}</td>
      <td class="mono">${metrics.total.misses}</td><td class="mono">${metrics.total.positives}</td></tr></tfoot></table>`;
}

function currentSection({profile, current, sample_size, sample_sha256}) {
  return `<section class="threshold-current"><div class="section-line"><h3>现行阈值与留出集表现</h3>
    <span class="mono">${sample_size} 条留出样本</span></div>
    <p class="muted">生效阈值来自${profile.origin === 'trial' ? ` ${dateTime(profile.published_at)} 发布的试算` : '初始演示规则'}。
      漏检指留出集中实际高风险、但概率低于该材质阈值而未开箱的样本。</p>
    ${metricTable(current, profile.thresholds)}
    <p class="fingerprint mono">样本指纹 ${e(sample_sha256)}</p></section>`;
}

function comparisonTable(trial) {
  const baseline = trial.results.baseline;
  const proposed = trial.results.proposed;
  return `<table class="metric-table"><thead><tr><th>材质</th><th>阈值（现行 → 试算）</th>
    <th>开箱数（现行 → 试算）</th><th>漏检（现行 → 试算）</th></tr></thead><tbody>
    ${materialNames.map((material) => {
      const before = baseline.materials[material];
      const after = proposed.materials[material];
      return `<tr><td>${e(materials[material])}</td>
        <td class="mono">${ratio(before.threshold)} → ${ratio(after.threshold)}</td>
        <td class="mono">${before.unpacked} → ${after.unpacked}</td>
        <td class="mono">${before.misses} → ${after.misses}</td></tr>`;
    }).join('')}
    </tbody><tfoot><tr><td>合计</td><td class="mono">—</td>
      <td class="mono">${baseline.total.unpacked} → ${proposed.total.unpacked}</td>
      <td class="mono">${baseline.total.misses} → ${proposed.total.misses}</td></tr></tfoot></table>`;
}

function trialSection(trial, staff) {
  if (!trial) return `<section class="threshold-trial"><h3>试算结果</h3><p class="empty">还没有试算记录。主管可调整各材质阈值后试算。</p></section>`;
  const stale = trial.stale;
  const running = trial.status === 'running';
  const reason = (trial.ineligibility ?? []).join('；');
  return `<section class="threshold-trial"><div class="section-line"><h3>试算 #${trial.id} ${stamp(trial.status,
      {running: '试算中', completed: trial.eligible ? '可发布' : '不可发布', failed: '试算失败', published: '已发布'})}</h3>
    <span class="mono">${dateTime(trial.created_at)}</span></div>
    <p class="muted">与 ${trial.baseline_profile_id} 号现行配置比较，沿用同一批
      ${trial.results?.proposed?.total.sample_size ?? '—'} 条留出样本。第 ${trial.attempts} 次尝试${
      trial.computed_at ? ` · 完成于 ${dateTime(trial.computed_at)}` : ''}。</p>
    ${trial.results ? comparisonTable(trial) : ''}
    ${running ? '<p class="muted">试算在后台进行，本页会自动刷新。</p>' : ''}
    ${trial.status === 'failed' ? `<p class="alert">${e(trial.error ?? '试算中断')} 主管可重新提交同一组阈值，系统沿用原样本续跑。</p>` : ''}
    ${trial.status === 'completed' && !trial.eligible ? `<p class="alert">未达发布条件：${e(reason)}</p>` : ''}
    ${trial.status === 'completed' && trial.eligible && stale ? '<p class="alert">试算后现行阈值已调整，需用当前阈值重新试算。</p>' : ''}
    ${trial.status === 'published' ? '<p class="success-line">已发布。新入队批次使用这套阈值；已排队批次仍沿用入队时的阈值。</p>' : ''}
    ${staff.role === 'supervisor' && trial.status === 'completed' && trial.eligible && !stale
      ? `<div class="form-actions"><button data-publish="${trial.id}">发布这套阈值</button></div>` : ''}
  </section>`;
}

function proposalForm(profile, staff) {
  if (staff.role !== 'supervisor') return '';
  return `<section class="threshold-proposal"><h3>试算新阈值</h3>
    <p class="muted">以现行阈值为基线，用同一批留出样本比较。总开箱数和各材质漏检都不能增加，且至少一类漏检减少才可发布。</p>
    <form data-action="thresholds"><div class="form-grid">${materialNames.map((material) => field(
      materials[material], material, {type: 'number', value: profile.thresholds[material], min: 0.05, max: 0.95, step: 0.001})).join('')}</div>
      <div class="form-actions"><span>阈值取 0.05 至 0.95，按材质各自判定。</span><button>试算</button></div></form></section>`;
}

export function thresholdPanel({staff, thresholds}) {
  if (!thresholds) return '';
  return `<section class="threshold-board"><h2>阈值配置</h2>
    <div class="threshold-workbench">${currentSection(thresholds)}
      ${trialSection(thresholds.trial, staff)}
      ${proposalForm(thresholds.profile, staff)}</div></section>`;
}
