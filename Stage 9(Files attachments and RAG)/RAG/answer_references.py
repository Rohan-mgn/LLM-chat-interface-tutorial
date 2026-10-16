"""Resolve explicit ordinal references to visible answer structure, not documents."""
import re

ORDINALS = {word:i+1 for i,word in enumerate(
    ("first","second","third","fourth","fifth","sixth","seventh","eighth","ninth","tenth"))}
REFERENCE = re.compile(r"\b(?:(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|[1-9][0-9]?(?:st|nd|rd|th)?)\s+(point|bullet|item|heading)|(point|bullet|item|heading)\s+([1-9][0-9]?))\b",re.I)

def requested(question):
    matches=list(REFERENCE.finditer(question))
    if not matches:return None
    if re.search(r"\b(?:in|of|from)\s+(?:the\s+)?(?:document|file|pdf)\b",question,re.I):
        return None
    if len(matches)!=1:
        raise ValueError("Please identify one point from the previous answer at a time.")
    m=matches[0]
    value=(m[1] or m[4]).lower()
    ordinal=ORDINALS.get(value) or int(re.match(r"\d+",value)[0])
    return ordinal,(m[2] or m[3]).lower()

def select(text,reference):
    """Conservative Markdown structure; ambiguous independent lists need clarification.

    Fenced code, blockquotes and nested bullets are not separate answer points.
    The selected passage remains an unverified prior assistant statement.
    """
    if len(text)>24000:
        raise ValueError("Please quote the point you mean; the previous answer is too long to resolve safely.")
    ordinal,kind=reference
    lines=text.splitlines(keepends=True)
    visible=[];offset=0;fence=None
    for line in lines:
        marker=re.match(r" {0,3}(`{3,}|~{3,})",line)
        if marker:
            run=marker[1]
            if fence is None:fence=(run[0],len(run))
            elif run[0]==fence[0] and len(run)>=fence[1] and not line[marker.end():].strip():fence=None
            visible.append((offset,line,False))
        else:visible.append((offset,line,fence is None and not line.lstrip().startswith(">")))
        offset+=len(line)
    # Heading sections are selected by structural level, never by document headings.
    headings=[]
    for offset,line,show in visible:
        h=re.match(r" {0,3}(#{1,6})\s+(.+)",line) if show else None
        if h:headings.append((offset,len(h[1]),h[2]))
    heading_groups=[]
    if headings:
        level=min(h[1] for h in headings)
        if sum(h[1]==level for h in headings)==1 and any(h[1]>level for h in headings):
            level=min(h[1] for h in headings if h[1]>level)
        starts=[h[0] for h in headings if h[1]==level]
        heading_groups=[[text[a:next((p for p,l,_ in headings if p>a and l<=level),len(text))].strip() for a in starts]]
    bullets=[]
    for offset,line,show in visible:
        m=re.match(r"( *)(?:(\d+)[.)]|([-+*]))\s+\S",line) if show else None
        if m:bullets.append((offset,len(m[1]),"ordered" if m[2] else "bullet"))
    lists=[]
    if bullets:
        indent=min(b[1] for b in bullets)
        top=[b for b in bullets if b[1]==indent]
        group=[];previous_end=None;previous_kind=None
        for index,(start,_,style) in enumerate(top):
            stop=top[index+1][0] if index+1<len(top) else len(text)
            # A new heading or top-level prose paragraph ends this item/list.
            body_end=stop
            for pos,line,show in visible:
                if pos<=start or pos>=stop or not show or not line.strip():continue
                if re.match(r" {0,3}#{1,6}\s",line) or (not line.startswith(" ")):
                    body_end=pos;break
            if group and (previous_kind!=style or previous_end!=start):
                lists.append((previous_kind,group));group=[]
            group.append(text[start:body_end].strip())
            previous_end=body_end;previous_kind=style
        if group:lists.append((previous_kind,group))
    if kind=="heading":groups=heading_groups
    elif kind=="bullet":groups=[g for style,g in lists if style=="bullet"]
    else:groups=[g for _,g in lists] or heading_groups
    eligible=[g for g in groups if len(g)>=ordinal]
    if len(eligible)!=1:
        raise ValueError("Please quote the point you mean; the previous answer has no unique matching list or heading.")
    chosen=eligible[0][ordinal-1]
    if len(chosen)>1800:
        raise ValueError("Please quote a shorter part of that point so I can resolve it accurately.")
    return {"ordinal":ordinal,"kind":kind,"text":chosen,
            "source_ids":list(dict.fromkeys(re.findall(r"\[(SOURCE_[a-f0-9]{24})\]",chosen)))}
