"""Bind loose fasteners to the contact of two supplied assemblies."""
def exterior_translation(body: dict, context: list[dict], axis: int, margin: float) -> list[float]:
    """Choose the nearest exterior plane on the proven fastener insertion axis."""
    if not context or margin<=0:raise ValueError('Native context and positive clearance required')
    low=min(b['min'][axis] for b in context);high=max(b['max'][axis] for b in context)
    negative=max(margin,body['max'][axis]-low+margin)
    positive=max(margin,high-body['min'][axis]+margin)
    vector=[0.,0.,0.];vector[axis]=-negative if negative<=positive else positive
    return vector

def joint_fasteners(graph: dict, candidates: list[str], component: str, foundation: str, completed: set[str]) -> list[str]:
    nodes={n['occurrence_id']:n for n in graph['occurrences']}
    def box(o):
        b=nodes[o].get('bounds_root',{})
        if b.get('status')!='available':raise ValueError('Joint selection requires native CAD bounds')
        return b
    a,b=box(component),box(foundation)
    lo=[max(a['min'][i],b['min'][i]) for i in range(3)]
    hi=[min(a['max'][i],b['max'][i]) for i in range(3)]
    if any(lo[i]>hi[i]+1e-6 for i in range(3)):raise ValueError('No proven contact bounds for this assembly pair')
    chosen=[]
    for o in candidates:
        c=box(o)
        if not all(c['max'][i]>=lo[i]-1e-6 and c['min'][i]<=hi[i]+1e-6 for i in range(3)):continue
        refs=[e.get('assembly_reference') for e in graph['constraints'] if e['occurrences'][0]==o]
        physical=[r['occurrence_id'] for r in refs if r and r.get('item_type')=='SURFACE']
        if not physical:continue
        if any(not any(r==v or r.startswith(v+'/') for v in completed) for r in physical):continue
        chosen.append(o)
    return chosen
