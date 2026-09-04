# CACHE MIND — easy cheat sheet (Hinglish)

Jury round se pehle 5 min mein padhne wali cheez. Bas yeh yaad rakho.

---

## Ek line mein poora project

> **"Normal cache sirf yaad rakhta hai kya recent use hua. Hamara CACHE MIND
> soch-ta hai — kya rakhna hai, KAHAN rakhna hai (fast RAM ya slow storage),
> aur kab khud ko badalna hai — sab automatic, live traffic dekh ke."**

---

## Problem kya tha (1 min)

- Normal cache (LRU/LFU) sirf dekhta hai "recently kya use hua" — usko yeh
  pata hi nahi ki koi object **regenerate karna kitna mehenga/slow** hai.
- Jab cache full ho jaata hai, wo purani cheez **fek deta hai (evict)**.
  Agli baar wahi cheez maangi to seedha origin se lao — **bahut slow, bahut mehenga**.
- Humne isse fix kiya do tarike se: (1) fekne ki jagah **neeche wale slow-storage
  mein daalo** (evict nahi, demote), (2) rakhne ka decision sirf "recent hai kya"
  pe nahi, balki **"isko wapas banane mein kitna paisa/time lagega"** pe lo.

---

## Architecture — bas itna samjho

```
Traffic aata hai
   → CACHE MIND dekh-ta hai: kaun sa object important hai
   → decide karta hai: RAM (fast) mein rakhu, ya Redis (medium) mein,
                        ya cold storage (slow) mein, ya fek du
   → agar sab jagah miss ho jaaye → tabhi origin se laata hai (sabse slow)
```

3 tier hai — jaise ghar mein cheezein rakhte ho:
- **L1 = RAM** → jeb mein rakhi cheez (turant milti hai, jagah kam)
- **L2 = Redis** → almirah mein rakhi cheez (thoda time lagta hai, jagah zyada)
- **L3 = Cold storage** → godown mein rakhi cheez (der lagti hai, sabse sasta)
- **Origin** → dukaan se dobara khareedna (sabse slow, sabse mehenga)

**Sabse bada trick:** competitor cache (LRU/GDSF) ke paas sirf ek hi jagah
(RAM) hai — full hua to seedha fek dete hai → agli baar dukaan jaana padta hai.
Hamare paas 3 jagah hai, isliye cheez RAM se nikle bhi to *almirah* mein hai,
*godown* nahi jaana padta.

---

## Ek-ek cheez ka kaam (bolne ke liye)

| Cheez | Kya karti hai (ek line) |
|---|---|
| **scoring.py** | har object ko number deta hai — "isko rakhna kitna zaroori hai" |
| **GDSF** (uss number ka core) | purana, proven formula — kitni baar use hua × banane mein kitna kharcha ÷ size kitna bada |
| **predict.py** | dekhta hai kaun sa object jaldi phir se maanga jaayega (jaise pattern se guess karna) |
| **bandit.py** | har thodi der mein khud decide karta hai "abhi konsi strategy best hai" — traffic dekh ke seekhta hai |
| **autoscaler.py** | RAM/Redis/storage ka size khud badhata-ghatata hai — jaisa traffic waisa size |
| **cachemind.py** | sabko jodta hai — har round mein: dekho → predict karo → number do → jagah decide karo → action lo → seekho |
| **baselines/** | comparison ke liye normal LRU/LFU/GDSF bhi bana rakhe hai — taaki proof mile hum inse behtar hai |
| **workload/** | fake traffic banata hai testing ke liye — 2 tarah ki app (ek "API type", ek "AI-recommendation type") |
| **benchmark/** | sabko same rules pe test karta hai aur result nikalta hai (kaun sasta, kaun fast) |
| **api/ + dashboard/** | live chalake dikhane wala UI — graphs, tiers, decisions sab real-time |

---

## ML kaha hai (agar poochein)

Do jagah, dono **live seekhte hai, koi training pehle se nahi**:

1. **Bandit** — har round apni strategy (weights) khud badalta hai, jo bhi
   strategy best result de rahi ho use zyada use karta hai. (Explore vs exploit.)
2. **Predictor** — har object ka pattern dekh ke guess karta hai "yeh jaldi
   phir maanga jaayega ya nahi" — usi se prefetch aur value decide hota hai.

**Honest baat bolna:** "Asli jeet tiering (3 jagah rakhna) aur smart refresh se
aa rahi hai. ML humein har tarah ke traffic mein adaptive banata hai — khaas
kar jab traffic pattern achanak badal jaaye."

---

## Result (yeh number bolna)

- Normal GDSF ke comparison mein: **71-82% kam cost**
- Fair comparison (same 3-tier hardware, bas placement smart) mein bhi:
  **~50% kam cost**, latency **6ms flat** (baaki ki 14-22ms)
- Har cache-size pe sabse sasta — sirf ek lucky size pe nahi

---

## Agar poochein "dono profile alag kaise hai"

> "`api` profile mein miss hone ka dard **paisa** hai (paid API calls).
> `recsys` profile mein miss hone ka dard **time** hai (AI model dobara chalana
> padta hai, 2 second tak lagta hai). Humne same engine, bina kuch badle,
> dono pe chalaya — dono jeete."

---

## Agar poochein "sab baseline se hum itna behtar kyu dikh rahe"

> "Bade gap ka reason mostly yeh hai ki baseline single-tier hai — full hote
> hi fek dete hai. Isliye humne fair comparison bhi banaya (same 3-tier
> hardware, bas dumb placement) — usme bhi hum ~50% jeetate hai kyuki hum
> *sahi jagah* rakhte hai, random nahi."
