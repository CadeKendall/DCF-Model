const $ = id => document.getElementById(id);
const pct = value => value == null ? 'Unavailable' : (value * 100).toFixed(2) + '%';
const num = value => value == null ? 'Unavailable' : value.toLocaleString('en-US', {maximumFractionDigits: 2});
let latest = null;
let pending = false;
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
if (!reduced.matches && 'IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    entry.target.classList.toggle('offscreen', !entry.isIntersecting);
  }), {threshold: 0.04});
  document.querySelectorAll('.reveal').forEach(node => observer.observe(node));
}
window.addEventListener('scroll', () => {
  const distance = document.documentElement.scrollHeight - window.innerHeight;
  $('scroll-progress').style.width = (distance > 0 ? window.scrollY / distance * 100 : 0) + '%';
}, {passive: true});
try {document.body.classList.toggle('dark', localStorage.getItem('tiger-theme') === 'dark');} catch {}
$('theme').addEventListener('click', () => {
  document.body.classList.toggle('dark');
  try {localStorage.setItem('tiger-theme', document.body.classList.contains('dark') ? 'dark' : 'light');} catch {}
  drawChart(latest?.series);
});
$('corporate-tax').addEventListener('change', () => {
  $('tax-description').textContent = $('corporate-tax').checked ? 'On · apply 21% to the debt tax shield' : 'Off · calculate taxes ÷ pretax income';
});
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240000);
  try {
    const response = await fetch(path, {...options, signal: controller.signal});
    const data = await response.json();
    if (!response.ok) {
      const message = Array.isArray(data.detail) ? data.detail.map(e => e.msg).join(' ') : data.detail;
      throw new Error(message || 'The request could not be completed.');
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Wait for any running analysis to finish, then retry.');
    if (error instanceof TypeError) throw new Error('Cannot reach the local app. Keep its PowerShell window open.');
    throw error;
  } finally {clearTimeout(timeout);}
}
function showError(id, message) {$(id).textContent = message; $(id).hidden = false;}
api('/api/status').then(status => {
  $('connection').textContent = status.connected ? 'WRDS connected' : 'Preview · WRDS sign-in required';
  $('connection').classList.toggle('connected', status.connected);
  $('connection-help').hidden = status.connected;
  $('end').max = status.today; $('start').max = status.today;
}).catch(() => {$('connection').textContent = 'Server unavailable';});
function invalidate() {
  if (!latest) return;
  latest = null;
  $('download').disabled = true; $('download-csv').disabled = true;
  $('result-tag').textContent = 'INPUTS CHANGED';
  $('result-caption').textContent = 'Displayed results are from your previous run. Run again to apply these inputs.';
}
$('analysis-form').addEventListener('input', invalidate);
$('analysis-form').addEventListener('submit', async event => {
  event.preventDefault(); if (pending) return;
  $('analysis-error').hidden = true;
  if ($('start').value >= $('end').value) {showError('analysis-error', 'Beginning date must be before ending date.'); return;}
  pending = true;
  const payload = {ticker: $('ticker').value.trim().toUpperCase(), proxy: $('proxy').value,
    start: $('start').value, end: $('end').value, fiscal_year: $('fiscal-year').value ? Number($('fiscal-year').value) : null,
    corporate_tax: $('corporate-tax').checked};
  latest = null;
  $('download').disabled = true; $('download-csv').disabled = true;
  $('results').setAttribute('aria-busy', 'true'); $('loading').hidden = false;
  const controls = [...$('analysis-form').querySelectorAll('input,select,button')];
  controls.forEach(node => node.disabled = true);
  try {
    latest = await api('/api/analyze', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    render(latest);
  } catch(error) {
    showError('analysis-error', error.message);
    $('result-tag').textContent = 'ANALYSIS UNAVAILABLE';
    $('result-caption').textContent = 'No new result was generated. Any displayed values belong to the previous run.';
  } finally {
    pending = false; controls.forEach(node => node.disabled = false);
    $('results').setAttribute('aria-busy', 'false'); $('loading').hidden = true;
  }
});
function render(result) {
  const m = result.metrics;
  $('wacc').replaceChildren(document.createTextNode((m.wacc * 100).toFixed(2)), Object.assign(document.createElement('span'), {textContent:'%'}));
  $('company-name').textContent = `${result.company} · ${result.ticker}`;
  $('result-tag').textContent = `${result.ticker} / ${result.proxy}`;
  $('result-caption').textContent = `${result.start} → ${result.end} · ${result.observations.toLocaleString()} shared trading days`;
  ['cost_equity','after_tax_debt','risk_free','market_return','tax_rate','cost_debt','equity_weight','debt_weight'].forEach(key => $(key.replaceAll('_','-')).textContent = pct(m[key]));
  $('beta').textContent = m.beta.toFixed(3); $('beta-caption').textContent = `Relative to ${result.proxy}`;
  $('equity').textContent = num(m.equity); $('debt').textContent = num(m.debt);
  $('equity-bar').style.width = m.equity_weight*100+'%'; $('debt-bar').style.width = m.debt_weight*100+'%';
  $('stock-legend').textContent = result.ticker; $('market-legend').textContent = result.proxy;
  $('chart-caption').textContent = `${result.series[0].date} → ${result.series.at(-1).date} · $100 compounded over shared dates. Hover a line for daily values.`;
  drawChart(result.series);
  const audit = $('audit-content'); audit.replaceChildren();
  const rows = [
    ['Financial period', `FY ${result.fiscal_year} · ends ${result.fiscal_end}`],
    ['Compustat company ID', result.gvkey], ['Treasury observation',result.risk_free_date],
    ['Tax selection',result.tax_mode], ['Calculated tax rate',pct(m.effective_tax_rate)],
    ['Average debt · $ millions',num(m.average_debt)], ['Interest expense · $ millions',num(m.interest)],
    ['Income taxes · $ millions',num(m.taxes)], ['Pretax income · $ millions',num(m.pretax_income)],
    ['Market daily observations',num(result.market_observations)],
    ['Returns source',result.sources.returns], ['Financial source',result.sources.financials],
    ['Risk-free source',result.sources.risk_free], ['Retrieved (UTC)',result.retrieved_at]
  ];
  rows.forEach(([label,value]) => {
    const row = document.createElement('div'); row.className='audit-row';
    row.append(Object.assign(document.createElement('span'),{textContent:label}), Object.assign(document.createElement('span'),{textContent:value})); audit.append(row);
  });
  $('warnings').replaceChildren(...result.warnings.map(warning => Object.assign(document.createElement('p'), {textContent:warning})));
  $('warnings').hidden = !result.warnings.length;
  $('download').disabled=false; $('download-csv').disabled=false;
}
const ns = 'http://www.w3.org/2000/svg';
function svgElement(tag, attrs, text) {
  const node = document.createElementNS(ns,tag); Object.entries(attrs).forEach(([key,value]) => node.setAttribute(key,value));
  if (text !== undefined) node.textContent=text; return node;
}
function drawChart(series) {
  const svg=$('chart'); svg.replaceChildren();
  const style=getComputedStyle(document.body), line=style.getPropertyValue('--line'), muted=style.getPropertyValue('--muted');
  const values=series?.flatMap(point=>[point.stock_index,point.market_index]) || [80,120];
  const low=Math.min(100,...values), high=Math.max(100,...values);
  const margin=Math.max((high-low)*.12,2), bottom=low-margin, top=high+margin;
  const y=value=>225-(value-bottom)/(top-bottom)*205;
  const x=index=>55+index/Math.max(1,(series?.length || 2)-1)*690;
  for(let i=0;i<5;i++) {
    const value=bottom+(top-bottom)*i/4, yy=y(value);
    svg.append(svgElement('line',{x1:55,y1:yy,x2:745,y2:yy,stroke:line,'stroke-dasharray':'3 5'}));
    if(series) svg.append(svgElement('text',{x:43,y:yy+4,fill:muted,'font-size':11,'text-anchor':'end'},'$'+Math.round(value)));
  }
  $('chart-empty').hidden=!!series;
  if(!series) return;
  svg.setAttribute('aria-label',`Growth of $100 for ${$('stock-legend').textContent} and ${$('market-legend').textContent}. Daily values are available in the CSV download.`);
  for(const [field,color] of [['stock_index',style.getPropertyValue('--orange')],['market_index',style.getPropertyValue('--purple')]]) {
    svg.append(svgElement('polyline',{points:series.map((p,i)=>`${x(i)},${y(p[field])}`).join(' '),fill:'none',stroke:color,'stroke-width':2.5,'stroke-linejoin':'round'}));
  }
  for(const index of [...new Set([0,Math.floor(series.length/2),series.length-1])]) {
    svg.append(svgElement('text',{x:x(index),y:258,fill:muted,'font-size':11,'text-anchor':index===0?'start':index===series.length-1?'end':'middle'},series[index].date));
  }
  series.forEach((p,i)=>{
    const hit=svgElement('rect',{x:x(i)-2,y:15,width:Math.max(4,690/series.length),height:215,fill:'transparent'});
    hit.append(svgElement('title',{},`${p.date}\n${$('stock-legend').textContent}: $${p.stock_index.toFixed(2)}\n${$('market-legend').textContent}: $${p.market_index.toFixed(2)}`)); svg.append(hit);
  });
}
drawChart();
$('weather-form').addEventListener('submit',async event=>{
  event.preventDefault(); $('weather-error').hidden=true; $('weather-button').disabled=true;
  $('weather-result').textContent='Checking local conditions…';
  try {
    const w=await api('/api/weather/'+encodeURIComponent($('zip').value));
    const reading=document.createElement('div'); reading.className='weather-reading';
    const description=document.createElement('div');
    description.append(Object.assign(document.createElement('p'),{textContent:w.location}),Object.assign(document.createElement('p'),{textContent:w.description}));
    reading.append(Object.assign(document.createElement('strong'),{textContent:Math.round(w.temperature_2m)+'°F'}),description);
    const meta=Object.assign(document.createElement('p'),{className:'weather-meta',textContent:`Feels like ${Math.round(w.apparent_temperature)}°F · Wind ${w.wind_speed_10m} mph · Humidity ${w.relative_humidity_2m}%`});
    const time=Object.assign(document.createElement('p'),{className:'weather-meta',textContent:`${w.time.replace('T',' ')} · ${w.timezone}`});
    $('weather-result').replaceChildren(reading,meta,time);
  } catch(error) {$('weather-result').replaceChildren();showError('weather-error',error.message);}
  finally {$('weather-button').disabled=false;}
});
function download(content,type,name) {
  const url=URL.createObjectURL(new Blob([content],{type}));
  const link=Object.assign(document.createElement('a'),{href:url,download:name});link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('download').addEventListener('click',()=>{if(latest)download(JSON.stringify(latest,null,2),'application/json',`${latest.ticker}_${latest.proxy}_analysis.json`);});
$('download-csv').addEventListener('click',()=>{
  if(!latest)return;
  const fields=['date','stock','market','stock_index','market_index'];
  download([fields.join(','),...latest.series.map(row=>fields.map(key=>row[key]).join(','))].join('\r\n'),'text/csv',`${latest.ticker}_${latest.proxy}_returns.csv`);
});
