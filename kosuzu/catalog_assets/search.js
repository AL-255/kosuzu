'use strict';
(function(root) {
  const stop = new Set(['a','an','the','i','me','my','want','need','looking','look','for','please','some','find','with','of','and','to','parts','part']);
  function normalize(value) { return String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/ω/g,'ohm').replace(/µ|μ/g,'u').replace(/[^\p{L}\p{N}]+/gu,' ').trim(); }
  function distance(a,b,limit) {
    if (Math.abs(a.length-b.length)>limit) return limit+1;
    let prev=Array.from({length:b.length+1},(_,i)=>i), older=null;
    for(let i=1;i<=a.length;i++) {
      const row=[i]; let min=i;
      for(let j=1;j<=b.length;j++) {
        row[j]=Math.min(row[j-1]+1,prev[j]+1,prev[j-1]+(a[i-1]===b[j-1]?0:1));
        if(older && i>1 && j>1 && a[i-1]===b[j-2] && a[i-2]===b[j-1]) row[j]=Math.min(row[j],older[j-2]+1);
        min=Math.min(min,row[j]);
      }
      if(min>limit) return limit+1;
      older=prev; prev=row;
    }
    return prev[b.length];
  }
  function index(data) {
    const boxes=new Map(data.boxes.map(b=>[b.id,b]));
    return data.parts.map(part=>{
      const placements=Object.entries(part.boxes).map(([id,quantity])=>({id,quantity,name:id?boxes.get(id)?.name || 'Unknown box':'Unboxed',description:boxes.get(id)?.description || ''}));
      const fields=[[part.mpn,12],[part.supplier_code,11],[part.manufacturer,4],[part.description,4],[part.category,3],[part.package,3],[part.location,2],[part.supplier,2],...Object.entries(part.attributes).map(([k,v])=>[k+' '+v,3]),...placements.map(b=>[b.name+' '+b.description,3])];
      const words=new Map();
      for(const [value,weight] of fields) for(const word of normalize(value).split(' ').filter(Boolean)) {
        const bounded=word.slice(0,80); words.set(bounded,Math.max(weight,words.get(bounded)||0));
      }
      const identifiers=[part.mpn,part.supplier_code].map(v=>normalize(v).replaceAll(' ',''));
      for(const id of identifiers) if(id) words.set(id.slice(0,80),12);
      return {part,placements,words:[...words],identifiers};
    });
  }
  function search(rows,query,filters={}) {
    const normalized=normalize(query.slice(0,160));
    const terms=[...new Set(normalized.split(' ').filter(w=>w && !stop.has(w)))].slice(0,20);
    const compact=normalized.replaceAll(' ','');
    const result=[];
    for(const row of rows) {
      const p=row.part;
      if(filters.category && p.category!==filters.category || filters.box && !row.placements.some(b=>(b.id || '__unboxed__')===filters.box) || filters.stock && p.quantity===0) continue;
      let score=0;
      if(compact && row.identifiers.includes(compact)) score=1000;
      else if(compact && row.identifiers.some(id=>id.includes(compact))) score=100;
      else for(const term of terms) {
        let best=0;
        for(const [word,weight] of row.words) {
          if(/^\d+$/.test(term) && word.match(/^\d+/)?.[0]!==term) continue;
          if(word===term) best=Math.max(best,weight+10);
          else if(word.startsWith(term)) best=Math.max(best,weight+6);
          else if(term.length>=3 && word.includes(term)) best=Math.max(best,weight+3);
          else if(term.length>=4) {
            const limit=term.length>=8?2:1;
            if(distance(term,word,limit)<=limit) best=Math.max(best,weight+1);
          }
        }
        if(!best) { score=-1; break; }
        score+=best;
      }
      if(score>=0) result.push({...row,score});
    }
    return result.sort((a,b)=>b.score-a.score || a.part.mpn.localeCompare(b.part.mpn) || a.part.id.localeCompare(b.part.id));
  }
  const api={normalize,distance,index,search};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.CatalogSearch=api;
})(globalThis);
