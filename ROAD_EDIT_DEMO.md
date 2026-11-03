# নির্দিষ্ট রাস্তার parameter বদলে route দেখানোর নিয়ম

**Dynamic replanning শুধু blockage-এর জন্য নয়।** Auto-run চালু থাকলে ছয়টি
scenario, global দিন/রাত, cost weight বা search limit বদলালেও আগে চালানো
algorithm-গুলো নতুন snapshot দিয়ে আবার চলে। Before/after টেবিল ও আগের পথের
overlay পাওয়া যায়। Auto-run বন্ধ থাকলে আগের ফল সরিয়ে দেওয়া হয়; নতুন ফলের
জন্য Run চাপতে হয়। নির্দিষ্ট রাস্তার Apply ও Remove বাটনও পুনরায় search চালায়।
নতুন search মানেই পথ অবশ্যই বদলাবে না; একই পথ নতুন অবস্থাতেও সেরা হতে পারে।

## স্যারের সামনে দেখাবেন যেভাবে

1. **Central demo region (small)** ও **normal_day** বেছে নিন। From/To ঠিক করে UCS ও A* চালান।
2. **Edit roads** খুলুন। **Edit the path of algorithm** থেকে যে algorithm-এর পথ দেখিয়েছেন সেটি বাছুন। BFS/DFS/IDS/GBFS-এর পথও বাছা যায়।
3. **Current route** দিয়ে একটি নির্দিষ্ট segment বাছুন, অথবা **Part of current route** দিয়ে route-এর কিছু অংশ বাছুন। একটি বড় রাস্তা একাধিক segment-এ ভাগ থাকতে পারে।
4. **Parameters to change** থেকে শুধু Traffic, শুধু Road condition, অথবা একসঙ্গে কয়েকটি parameter বাছুন। নির্বাচিত প্রতিটি রাস্তার বর্তমান মান টেবিলে দেখা যাবে।
5. মান বদলে **Apply parameters and replan** চাপুন। শুধু বাছা parameter বদলাবে; অন্য parameter আগের মানে থাকবে। আগে চালানো algorithm-গুলো নিজে থেকেই আবার চলবে।
6. **Before / after the last road edit** টেবিলে দেখুন: পথ বদলেছে কি না, আগের/পরের দূরত্ব, আগের cost, নতুন অবস্থায় আগের পথের cost, এবং নতুন পথের cost।
7. **Map & Run → Map**-এ আগের পথ ধূসর dashed, নতুন পথ algorithm-এর রঙে, আর বদলানো রাস্তা magenta dashed দেখা যাবে। **Path**-এ নতুন পথের node-গুলো ক্রমানুসারে দেখা যায়।
8. পুরোনো রাস্তা edit করার জন্য বাছাইটি রাখা হয়। নতুন route-এর রাস্তা edit করতে চাইলে **Use this algorithm's latest path for editing** চাপুন।
9. পরিবর্তনের নিচে **Remove** চাপলে ওই পরিবর্তন সরবে ও route আবার হিসাব হবে। **Reset road edits** সব পরিবর্তন সরিয়ে scenario-র মূল মানে ফেরায় এবং route পুনরায় চালায়।

## একসঙ্গে কয়েকটি condition

**Conditions on this part** থেকে Rain + Rush hour, অথবা Rain + Construction বাছুন। তারপর **Apply conditions and replan** চাপুন। একই অংশের condition বদলাতে টিক সরিয়ে আবার Apply করুন। সব টিক সরালে ওই condition edit মুছে যাবে।

Condition preset-গুলো নির্বাচিত scenario-র মূল মানের ওপর বসে। সংখ্যায় দেওয়া edit ও condition edit তালিকার ক্রমে বসে; পরে দেওয়া edit একই parameter-এর আগের পরিবর্তনকে override করতে পারে। Remove করলে বাকি edit-গুলো নতুন করে বসে, তাই overlap থাকলেও অন্য edit হারায় না।

**Apply to both directions of the same road** চালু থাকলে রাস্তার উভয় দিক বদলাবে। একই দুই junction-এর মধ্যে অন্য parallel road থাকলে সেটি বদলাবে না।

## কখন পথ বদলাবে, কখন বদলাবে না

- **UCS/A***: অন্য পথের মোট cost কম হলে পথ বদলাবে। সামান্য বাড়ালে একই পথ সেরা থাকতে পারে।
- **GBFS**: মূলত গন্তব্যের দূরত্ব দেখে, তাই penalty বাড়ালেও পথ একই থাকতে পারে।
- **BFS/DFS/IDS**: traffic/condition/safety cost অনুযায়ী পথ বাছাই করে না। Closure দিলে বন্ধ রাস্তা এড়িয়ে খোঁজে।
- **Road closed / Construction / Deep flood**: নির্বাচিত রাস্তা ব্যবহার করবে না। বিকল্প পথ না থাকলে `no_path` দেখানো সঠিক ফল।
- **Night safety**: global Night-time চালু থাকলে প্রভাব ফেলে। Local Night condition দিলে ওই রাস্তার daytime safety-তেও night penalty বসে।
- যে parameter-এর **weight = 0**, সেটি cost বদলায় না।
- **Incident label** শুধু চিহ্ন; cost বাড়াতে parameter বদলান অথবা condition preset বাছুন।

## পরীক্ষায় পাওয়া বাস্তব উদাহরণ

Demo region, normal_day, junction `2432078557` থেকে `3940938359`, default weights:

| অবস্থা | A*/UCS দূরত্ব | cost |
|---|---:|---:|
| প্রথম route | 2.2410 km | 3.7876 |
| ওই route-এ traffic = 5 হলে পুরোনো পথ | 2.2410 km | 7.5590 |
| নতুন করে পাওয়া route | 2.7474 km | 5.3501 |
| edit Remove করার পরে | 2.2410 km | 3.7876 |

এটি Streamlit-এর আসল control ও bundled road graph দিয়ে পরীক্ষা করা হয়েছে। অন্য trip/scenario/seed/weight-এ সংখ্যা আলাদা হবে।

অ্যাপ চালাতে project folder-এ:

```powershell
py -3.12 -m streamlit run app.py
```

পরীক্ষাগুলো চালাতে:

```powershell
py -3.12 -m pytest -q
```
