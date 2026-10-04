'use strict';
const $=id=>document.getElementById(id);
let rows=[],results=[],shown=0,timer;
function element(tag,text,className) { const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(className) node.className=className; return node; }
function link(label,url) {
  try { const parsed=new URL(url); if(parsed.protocol!=='https:' || parsed.username || parsed.password) return null; }
  catch { return null; }
  const node=element('a',label); node.href=url; node.target='_blank'; node.rel='noopener noreferrer'; return node;
}
function renderPart(row) {
  const p=row.part,card=element('article',undefined,'part'),head=element('div',undefined,'part-head');
  const title=element('div'); title.append(element('h2',p.mpn),element('p',p.manufacturer,'manufacturer'));
  const quantity=element('div',String(p.quantity),'quantity'); quantity.append(element('small','in stock')); head.append(title,quantity); card.append(head,element('p',p.description,'description'));
  card.append(element('p',[p.category,p.package].filter(Boolean).join(' · '),'metadata'));
  const placements=element('div',undefined,'placements');
  for(const b of row.placements) { const chip=element('span',`${b.name}: ${b.quantity}`); chip.title=b.description; placements.append(chip); }
  card.append(placements);
  if(p.location) card.append(element('p',p.location,'metadata'));
  const detail=element('details'),summary=element('summary','Specifications & links'); detail.append(summary);
  const specs=element('dl');
  for(const [key,value] of Object.entries(p.attributes)) specs.append(element('dt',key),element('dd',value));
  detail.append(specs,element('p',`${p.supplier} · ${p.supplier_code}`,'metadata'));
  const links=element('div',undefined,'links');
  for(const [label,url] of [['Supplier ↗',p.source_url],['Datasheet ↗',p.datasheet_url],['Picture ↗',p.image_url]]) { const node=link(label,url); if(node) links.append(node); }
  detail.append(links); card.append(detail); return card;
}
function more() { const end=Math.min(shown+40,results.length),fragment=document.createDocumentFragment(); for(;shown<end;shown++) fragment.append(renderPart(results[shown])); $('results').append(fragment); $('more').hidden=shown>=results.length; }
function search() {
  results=CatalogSearch.search(rows,$('search').value,{category:$('category').value,box:$('box').value,stock:$('stock').checked}); shown=0; $('results').replaceChildren();
  $('count').textContent=`${results.length} ${results.length===1?'component':'components'} found`;
  if(!results.length) $('results').append(element('div',rows.length?'No matching components. Try fewer words or clear the filters.':'No components yet. Published parts will appear here.','empty'));
  more();
}
async function load() {
  $('refresh').disabled=true; $('error').hidden=true;
  try {
    const response=await fetch('data.json',{cache:'no-store',credentials:'omit'});
    if(!response.ok) throw new Error();
    const data=await response.json();
    if(data.schema!==1 || !Array.isArray(data.parts) || !Array.isArray(data.boxes)) throw new Error();
    const indexed=CatalogSearch.index(data); rows=indexed;
    const selectedCategory=$('category').value,selectedBox=$('box').value;
    $('category').replaceChildren(new Option('All categories',''),...[...new Set(data.parts.map(p=>p.category).filter(Boolean))].sort().map(v=>new Option(v,v)));
    $('box').replaceChildren(new Option('All boxes',''),new Option('Unboxed','__unboxed__'),...data.boxes.slice().sort((a,b)=>a.name.localeCompare(b.name)).map(b=>new Option(b.name,b.id)));
    $('category').value=selectedCategory; $('box').value=selectedBox;
    $('snapshot').textContent=`${data.repo} · ${data.branch} · inventory revision ${data.revision}`;
    $('search').disabled=false; search();
  } catch {
    $('error').textContent=rows.length?'Could not refresh the snapshot. You can keep searching the loaded inventory.':'Could not load inventory. Try Refresh snapshot after the site finishes publishing.'; $('error').hidden=false;
    if(!rows.length) $('count').textContent='Inventory unavailable';
  } finally { $('refresh').disabled=false; }
}
$('search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(search,100);});
for(const id of ['category','box','stock']) $(id).addEventListener('change',search);
$('more').addEventListener('click',more); $('refresh').addEventListener('click',load); load();
