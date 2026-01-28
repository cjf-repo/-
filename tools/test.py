# import json
# def stats(path):
#     vals=[]
#     with open(path,'r',encoding='utf-8') as f:
#         for line in f:
#             r=json.loads(line)
#             if r.get("ok"):
#                 vals.append(float(r.get("latency_ms",0)))
#     vals.sort()
#     print(path, "n=",len(vals), "min=",vals[0], "p50=",vals[len(vals)//2], "max=",vals[-1])
# stats("out/时延性能/mutipath_baseline/nor_middle0.json")
# stats("out/时延性能/mutipath_baseline/mutipath_middle0.json")

import json
def zeros(path):
    z=0; n=0
    with open(path,'r',encoding='utf-8') as f:
        for line in f:
            r=json.loads(line)
            if r.get("ok"):
                n+=1
                if float(r.get("latency_ms",0))==0:
                    z+=1
    print(path, "zeros/ok=", z, "/", n)
zeros("out/时延性能/mutipath_baseline/nor_middle0.json")
zeros("out/时延性能/mutipath_baseline/mutipath_middle0.json")
