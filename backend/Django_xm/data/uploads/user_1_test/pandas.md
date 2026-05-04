所需数据

 [data](E:\数学建模\pythonProject\numpy\data) 



pandas除了处理数值之外（基于numpy），还能够帮助我们处理其他类型的数据

# Series一维，带标签数组

```python
import pandas as pd

# 通过列表创建Series
t = pd.Series([1, 2, 3, 4, 5], index=["戴兴", "向真", "方硕勋", "张树林", "薛玉武"])
print(t)
"""
戴兴     1
向真     2
方硕勋    3
张树林    4
薛玉武    5
dtype: int64
"""

# 通过字典创建Series
data = {"姓名":"戴兴","年龄":20,"电话号码":"15674458323"}
t1 = pd.Series(data)
print(t1)
"""
姓名               戴兴
年龄               20
电话号码    15674458323
dtype: object
"""
```

转换数据类型和numpy一样



## 取值

```python
import pandas as pd

# 通过列表创建Series
t = pd.Series([1, 2, 3, 4, 5], index=["戴兴", "向真", "方硕勋", "张树林", "薛玉武"])
# print(t)
"""
戴兴     1
向真     2
方硕勋    3
张树林    4
薛玉武    5
dtype: int64
"""
# 取值
print(t["方硕勋"], t.iloc[2])  # 3 

# 取连续的
print(t[:2])
"""
戴兴    1
向真    2
dtype: int64
"""

# 取不连续的
print(t.iloc[[1, 3]])
print(t[["向真", "张树林"]])
"""
向真     2
张树林    4
dtype: int64
"""
```



其他性质

```python
import pandas as pd

# 通过列表创建Series
t = pd.Series([1, 2, 3, 4, 5], index=["戴兴", "向真", "方硕勋", "张树林", "薛玉武"])
print(t[t>2])
"""
方硕勋    3
张树林    4
薛玉武    5
dtype: int64
"""

print(t.values)  # [1 2 3 4 5]
print(t.index)   # Index(['戴兴', '向真', '方硕勋', '张树林', '薛玉武'], dtype='object')
```







# DataFrame二维，Series容器



## 基础用法

### 创建DataFrame对象

```python
import pandas as pd
import numpy as np

t = pd.DataFrame(np.arange(12).reshape(3,4))
print(t)
"""
   0  1   2   3
0  0  1   2   3
1  4  5   6   7
2  8  9  10  11
"""
t.columns=["戴兴","向真","张树林","方硕勋"]
print(t)
"""
   戴兴  向真  张树林  方硕勋
0   0   1    2    3
1   4   5    6    7
2   8   9   10   11
"""
t.index=[2023,2024,2025]
print(t)
"""
      戴兴  向真  张树林  方硕勋
2023   0   1    2    3
2024   4   5    6    7
2025   8   9   10   11
"""
```



#### 通过字典创建

##### 法1 

```python
import pandas as pd
import numpy as np

data1 = {"姓名":["戴兴","迪丽纳尔","方硕勋"],"age":[20,19,20],"电话":[156,157,135]}
d1=pd.DataFrame(data1)
print(d1)
"""
     姓名  age   电话
0    戴兴   20  156
1  迪丽纳尔   19  157
2   方硕勋   20  135

"""
```



筛选需要的信息

```python
import pandas as pd

data1 = {"姓名": ["戴兴", "迪丽纳尔", "方硕勋"], "age": [20, 19, 20], "电话": [156, 157, 135]}
# data2 = [{"姓名": "戴兴", "age": 20, "电话": 156}, {"姓名": "迪丽纳尔", "age": 19, "电话": 157}, {"姓名": "方硕勋","age":None,
#                                                                                                  "电话": 135}]

choice_data = {}
choice_data["姓名"] = data1["姓名"]
choice_data["年龄"] = data1["age"]

d1 = pd.DataFrame(choice_data)
print(d1)
```



##### 法2 

```python
import pandas as pd

data2 = {"姓名": "戴兴", "age": 20, "电话": 156}, {"姓名": "迪丽纳尔", "age": 19, "电话": 157}, {"姓名": "方硕勋",
                                                                                                 "电话": 135}
d2 = pd.DataFrame(data2)

print(d2)
"""
     姓名   age   电话
0    戴兴  20.0  156
1  迪丽纳尔  19.0  157
2   方硕勋   NaN  135
"""
```



筛选需要的信息

```python
import pandas
data2 = [{"姓名": "戴兴", "age": 20, "电话": 156}, {"姓名": "迪丽纳尔", "age": 19, "电话": 157}, {"姓名": "方硕勋","age":None,"电话": 135}]
choice_data = []
for i in data2:
    temp = {}
    temp["姓名"] = i["姓名"]
    temp["年龄"] = i["age"]
    choice_data.append(temp)
print(choice_data)
d1 = pd.DataFrame(data2)

print(d1)
"""
[{'姓名': '戴兴', '年龄': 20}, {'姓名': '迪丽纳尔', '年龄': 19}, {'姓名': '方硕勋', '年龄': None}]
     姓名   age   电话
0    戴兴  20.0  156
1  迪丽纳尔  19.0  157
2   方硕勋   NaN  135
"""
```

DataFrame对象既有行索引，又有列索引

行索引，表明不同行。行索引叫index，axis=0

列索引，表明不同列。列索引叫columns，axis=1







### DataFrame的基础属性

- df.shape      # 行数 列数
- df.dtypes     # 列数据类型
- df.ndim        # 数据纬度
- df.index        # 行索引
- df.columns   # 列索引
- df.values       # 对象值，二维ndarray数组



### DataFrame整体情况查询

- df.head(3)	# 显示头部几行，默认5行
- df.tail(3)        # 显示末尾几行，默认5行
- df.info()         # 相关信息概览：行数、列数、列索引、列非空值个数、列类型、内存占用
- df.describe()  # 快速综合统计结果：计算、均值、标准差、最大值、四分位数、最小值



### 取数据

```python
import pandas as pd

df = pd.read_csv("./data/dogNames2.csv")

df = df.sort_values(by="Count_AnimalName", ascending=False)  # 通过by选择筛选对象,默认升序
"""
pandans取行或者列的注意事项
1. 方括号写数字，表示取行，对行操作
2. 写字符串，表示取列索引，对列进行操作
"""
print(df[1:6])
print(df[1:6]["Row_Labels"])  # 取行
"""
      Row_Labels  Count_AnimalName
9140         MAX              1153
2660     CHARLIE               856
3251        COCO               852
12368      ROCKY               823
8417        LOLA               795

9140         MAX
2660     CHARLIE
3251        COCO
12368      ROCKY
8417        LOLA
Name: Row_Labels, dtype: object
"""
```



加入条件进行数据筛选(& 是且，| 是或)

注意不同条件之间需要括号括起来

```python 
import pandas as pd

df = pd.read_csv("./data/dogNames2.csv")

df = df.sort_values(by="Count_AnimalName", ascending=False)  # 通过by选择筛选对象,默认升序

print(df[(df["Count_AnimalName"] > 700) & (df["Row_Labels"].str.len() > 4)])
"""
      Row_Labels  Count_AnimalName
1156       BELLA              1195
2660     CHARLIE               856
12368      ROCKY               823
8552       LUCKY               723
"""
```

上面的str.len是什么用法？看下面



#### 字符串方法

![image-20240927152042212](assets/image-20240927152042212.png)



### nan数据处理



判断nan

```python
import numpy as np
import pandas as pd

t3 = pd.DataFrame(np.arange(12).reshape(3, 4), index=list("abc"), columns=list('wxyz'))
t3.iloc[[2, 1], [0, 1]] = None
print(pd.notnull(t3))  # 也可以写成:t3.notnull()
print(t3.isnull())   # 和上面一样
"""
       w      x     y     z
a   True   True  True  True
b  False  False  True  True
c  False  False  True  True
       w      x      y      z
a  False  False  False  False
b   True   True  False  False
c   True   True  False  False
"""
```



#### 方法一：

```python
import numpy as np
import pandas as pd

# 判断nan
t3 = pd.DataFrame(np.arange(12).reshape(3, 4), index=list("abc"), columns=list('wxyz'))
t3.iloc[[2, 1], [0, 1]] = None
print(t3)
"""
     w    x   y   z
a  0.0  1.0   2   3
b  NaN  NaN   6   7
c  NaN  NaN  10  11
"""

# 对nan数据处理
# 方法一:直接删除
t3.dropna(axis=0, how='any', inplace=True)
# 默认是all及所i有的为nan的删除，axis为0就是看每一行为一组，为1就是每一列为一组，inplace:是否原地替换
print(t3)
"""
     w    x  y  z
a  0.0  1.0  2  3
"""
```



#### 方法二：

```python
import pandas as pd

data2 = {"姓名": "戴兴", "age": 20, "电话": 156}, {"姓名": "迪丽纳尔", "age": 19, "电话": 157}, {"姓名": "方硕勋",
                                                                                                 "电话": 135}
d2 = pd.DataFrame(data2)

print(d2)
"""
     姓名   age   电话
0    戴兴  20.0  156
1  迪丽纳尔  19.0  157
2   方硕勋   NaN  135
"""

print(d2.fillna(20)) # 写多少nan就填充成多少
"""
     姓名   age   电话
0    戴兴  20.0  156
1  迪丽纳尔  19.0  157
2   方硕勋  20.0  135
"""
```



添加改列的均值（pandas的mean函数计算均值不会将nan包含进去）

```python
import pandas as pd

data2 = {"姓名": "戴兴", "age": 20, }, {"姓名": "迪丽纳尔", "age": 19, "电话": 157}, {"姓名": "方硕勋",
                                                                                      "电话": 135}
d2 = pd.DataFrame(data2)

print(d2[["age", "电话"]].mean())  # 写多少nan就填充成多少
"""
age     19.5
电话     146.0
dtype: float64
"""

print(d2.fillna(d2[["电话", "age"]].mean()))
"""
     姓名   age     电话
0    戴兴  20.0  146.0
1  迪丽纳尔  19.0  157.0
2   方硕勋  19.5  135.0
"""

# 只对某一列的nan操作
d2["age"] = d2.age.fillna(d2["age"].mean())
print(d2)
"""
     姓名   age     电话
0    戴兴  20.0    NaN
1  迪丽纳尔  19.0  157.0
2   方硕勋  19.5  135.0
"""
```



### 案例（jc）



```python
import numpy as np
import pandas as pd

file_path = "./data/IMDB-Movie-Data.csv"
df = pd.read_csv(file_path)
# print(df.info())

# 获取平均评分
print(df["Rating"].mean())  # 6.723199999999999

# 导演人数
# 方法1
Dt = df["Director"].tolist()
Dt = set(Dt)  # 转换成集合，不会有重复
print(len(Dt))  # 644
# 方法2
Dt1 = df["Director"].unique()  # 去重并转换成列表
print(len(Dt1))  # 644

# 获取演员人数
Dt2 = df["Actors"].str.split(",").tolist()  # 是个二维的列表
Dt21 = {i for j in Dt2 for i in j}  # 直接转换成集合
print(len(Dt21))  # 2394

# 电影时长的最大最小值
max_runtime = df["Runtime (Minutes)"].max()
print(max_runtime)  # 191

max_runtime_index = df["Runtime (Minutes)"].argmax()
print(max_runtime_index)  # 828

min_runtime = df["Runtime (Minutes)"].min()
print(min_runtime)  # 66

min_runtime_index = df["Runtime (Minutes)"].argmin()
print(min_runtime_index)  # 793
```

---



#### 电影的评分分布

```python
import pandas as pd
from matplotlib import pyplot as plt

file_path = "./data/IMDB-Movie-Data.csv"
df = pd.read_csv(file_path)

# 电影的评分分布
runtime = df["Rating"]
max_runtime = max(runtime)
min_runtime = min(runtime)
print(min_runtime, max_runtime)
# 设置不等宽的组距，hist函数取到的会是一个左闭右开的区间
# 第一次画图可以知道1.9到3.5这个区间数值很小把这个区间的合并。
num_bin_list = [1.9, 3.5]
i = 3.5
while i <= max_runtime:
    i += 0.5
    num_bin_list.append(i)


plt.figure(figsize=(20, 8), dpi=80)
plt.hist(runtime, bins=num_bin_list)
plt.xticks(num_bin_list)
plt.show()
```

![image-20240928144813569](assets/image-20240928144813569.png)

---



#### 各类型的电影数量

步骤：

得到所有类型（去重），然后构造成对应形状的矩阵（列由类型觉得，行由电影数量决定），然后通过DataFram的性质将每个电影所包含的类在矩阵中表示出来，再通过pandas提供的sum函数得到每一类的数量

```python
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

file_path = "./data/IMDB-Movie-Data.csv"
df = pd.read_csv(file_path)

tempe_list = df["Genre"].str.split(",")
genre_list = list({j for i in tempe_list for j in i})  # 具体有哪些类

zeros_df = pd.DataFrame(np.zeros((df.shape[0], len(genre_list))), columns=genre_list)

for i in range(df.shape[0]):
    zeros_df.loc[i, tempe_list[i]] = 1  # 等价于zeros_df.loc[i,[Drama,···,Animation]] = 1

# 计算各类型具体值并排序

genre_count = zeros_df.sum(axis=0).sort_values(ascending=True)


plt.figure(figsize=(18,8))
plt.bar(genre_count.index,genre_count)
plt.show()
```



计算不同类型的数量并排序可以简写但是会有警告，不是很建议使用

```python
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

file_path = "./data/IMDB-Movie-Data.csv"
df = pd.read_csv(file_path)
# 将每部电影的类型组装成列表
tempe_list = df["Genre"].str.split(",")

# 直接统计各类型的数量，并进行排序(直接是将列表传入所以会警告)
genre_count = pd.value_counts([i for j in tempe_list for i in j],ascending=True)
plt.figure(figsize=(18,8))
plt.bar(genre_count.index,genre_count)
plt.show()
```



最优版

```python
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

file_path = "./data/IMDB-Movie-Data.csv"
df = pd.read_csv(file_path)
# 将每部电影的类型组装成列表
tempe_list = df["Genre"].str.split(",")

# 直接统计各类型的数量，并进行排序
x = pd.Series([i for j in tempe_list for i in j]).value_counts(ascending=True)

plt.figure(figsize=(18,8))
plt.bar(x.index,x)
plt.show()
```

![image-20240928161658666](assets/image-20240928161658666.png)



----



## 进阶用法



### 数组合并

#### join()

会用到**join()函数**默认情况下是把行索引相同的数据合并到一起

```python
import numpy as np
import pandas as pd

df1 = pd.DataFrame(np.zeros((3,3)),index=list('ABC'),columns=list('xyz'))
print(df1)

df2 = pd.DataFrame(np.zeros((2,4)),index=list('AB'),columns=list('abcd'))
print(df2)

print(df1.join(df2))
print(df2.join(df1))
"""
     x    y    z
A  0.0  0.0  0.0
B  0.0  0.0  0.0
C  0.0  0.0  0.0
     a    b    c    d
A  0.0  0.0  0.0  0.0
B  0.0  0.0  0.0  0.0
     x    y    z    a    b    c    d
A  0.0  0.0  0.0  0.0  0.0  0.0  0.0
B  0.0  0.0  0.0  0.0  0.0  0.0  0.0
C  0.0  0.0  0.0  NaN  NaN  NaN  NaN
     a    b    c    d    x    y    z
A  0.0  0.0  0.0  0.0  0.0  0.0  0.0
B  0.0  0.0  0.0  0.0  0.0  0.0  0.0
"""
```

可以看出是以外面的对象为基准进行**行方向的拼接**



#### merge()

参数

- `left`：要合并的左侧DataFrame；
- `right`：要合并的右侧DataFrame；
- `how`：指定合并的方式，默认为`'inner'`，可以是`'left'`、`'right'`、`'outer'`等；
- `on`：指定用于合并的列名，如果不指定，则默认使用两个DataFrame中的公共列进行合并。
- left_on、right_on：指定左侧DataFrame和右侧DataFrame用于合并的列名，可用于处理两个DataFrame中列名不同的情况；
- suffixes：指定在列名冲突时用于区分的后缀，默认为('_x', '_y')；
- indicator：在结果DataFrame中增加一个特殊的列，指示每行的合并方式，默认为False；
- validate：检查合并操作的类型是否有效，默认为None。





```python
import pandas as pd

# 创建 DataFrame df1
data1 = {'ID': [1, 2, 3, 4],
         'Name': ['Alice', 'Bob', 'Charlie', 'David']}
df1 = pd.DataFrame(data1)

# 创建 DataFrame df2
data2 = {'ID': [2, 3, 4, 5],
         'Age': [25, 30, 35, 40]}
df2 = pd.DataFrame(data2)

# 等价写法：merged_outer1 = df1.merge(df2,on="ID",how="outer")
merged_outer1 = pd.merge(df1, df2, on='ID', how='outer')  # outer取并集
print(merged_outer1)
"""
   ID     Name   Age
0   1    Alice   NaN
1   2      Bob  25.0
2   3  Charlie  30.0
3   4    David  35.0
4   5      NaN  40.0
"""

merged_outer2 = df1.merge(df2,on="ID")   # 默认取交集(how="inner")
print(merged_outer2)
"""
   ID     Name  Age
0   2      Bob   25
1   3  Charlie   30
2   4    David   35
"""
```

**交集**会将两个 DataFrame 按照共同的 `ID` 列进行内连接。结果将只包含两个 DataFrame 中都有的 `ID`。

**并集**会将两个 DataFrame 按照`ID`列进行拼接。结果将所有 DataFrame中的`ID`拼接在一起，没有对应数据的部分为nan。

---

左右基准

```python
import pandas as pd

# 创建 DataFrame df1
data1 = {'ID': [1, 2, 3, 4],
         'Name': [4,5,6,7]}
df1 = pd.DataFrame(data1)

# 创建 DataFrame df2
data2 = {'ID': [2, 3, 4, 5],
         'Age': [5, 3, 5, 4]}
df2 = pd.DataFrame(data2)

# merged_outer = df1.merge(df2, on='ID', how='left')等价写法
merged_outer = pd.merge(df1, df2, on='ID', how='left')  # 以左边为基准
print(merged_outer)
"""
   ID  Name  Age
0   1     4  NaN
1   2     5  5.0
2   3     6  3.0
3   4     7  5.0
"""

# merged_outer = df1.merge(df2, on='ID', how='right')等价写法
merged_outer = df1.merge(df2, on='ID', how='right')  # 以右边为基准
print(merged_outer)
"""
    ID     Name  Age
   ID  Name  Age
0   2   5.0    5
1   3   6.0    3
2   4   7.0    5
3   5   NaN    4
"""
```

---

不再单一以某一类型为合并对象

```python
import pandas as pd

# 创建 DataFrame df1
data1 = {'ID': [1, 2, 3, 4],
         'Name': [4, 5, 6, 7]}
df1 = pd.DataFrame(data1)

# 创建 DataFrame df2
data2 = {'ID': [2, 3, 4, 5],
         'Age': [5, 3, 5, 4]}
df2 = pd.DataFrame(data2)

# 等价不再写，与上一样
merged_outer = df1.merge(df2, how='outer', left_on="Name", right_on="ID")
print(merged_outer)
"""
    ID_x  Name  ID_y  Age
0   NaN   NaN   2.0  5.0
1   NaN   NaN   3.0  3.0
2   1.0   4.0   4.0  5.0
3   2.0   5.0   5.0  4.0
4   3.0   6.0   NaN  NaN
5   4.0   7.0   NaN  NaN
"""
merged_outer = df1.merge(df2, how='inner', left_on="Name", right_on="ID")
print(merged_outer)
"""
    ID_x  Name  ID_y  Age
0     1     4     4    5
1     2     5     5    4
"""
```

交集部分为各选择类型数值相等部分。并集是将其他部分合并没有数据的填nan



---



### 分组



#### 基础用法

```python
import pandas as pd
import numpy as np

file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)
group = df.groupby(by="Country")
print(group)  # 类型是DataFrameGroupBy可以进行遍历、调用、聚合方法
# <pandas.core.groupby.generic.DataFrameGroupBy object at 0x000002551D3698E0>

for i in group:
    print(i, type(i))
    """
    是个元组，第一个元素是按照什么分组下面的元素，第二个元素是第一个元素下面的数据
    ('ZA',            Brand  Store Number  ... Longitude Latitude
25597  Starbucks  47608-253804  ...     28.04   -26.15
25598  Starbucks  47640-253809  ...     28.28   -25.79
25599  Starbucks  47609-253286  ...     28.11   -26.02
    """
    
# 如果只是想要单独某个的分组数据可以直接t = df[df["Country"] == "ZA"]就是什么第二个元素
```



##### 计算分组后某些类型的数量

###### 对特定列进行计算

```python
import pandas as pd
import numpy as np

file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)
# pd.set_option('display.max_columns', None)
group = df.groupby(by="Country")

# 对所有列进行计算
print(group.count())
"""
         Brand  Store Number  Store Name  ...  Timezone  Longitude  Latitude
Country                                   ...                               
AD           1             1           1  ...         1          1         1
AE         144           144         144  ...       144        144       144
AR         108           108         108  ...       108        108       108
AT          18            18          18  ...        18         18        18
AU          22            22          22  ...        22         22        22
...        ...           ...         ...  ...       ...        ...       ...
TT           3             3           3  ...         3          3         3
TW         394           394         394  ...       394        394       394
US       13608         13608       13608  ...     13608      13608     13608
VN          25            25          25  ...        25         25        25
ZA           3             3           3  ...         3          3         3

[73 rows x 12 columns]
"""
# 对特定列进行计算
print(group[["Brand","Store Name"]].count())
"""
         Brand  Store Name
Country                   
AD           1           1
AE         144         144
AR         108         108
AT          18          18
AU          22          22
...        ...         ...
TT           3           3
TW         394         394
US       13608       13608
VN          25          25
ZA           3           3

[73 rows x 2 columns]
"""
```



###### 取特定行的数据

```python
import pandas as pd
import numpy as np

file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)
# pd.set_option('display.max_columns', None)
group = df.groupby(by="Country")

country_count = group.count()
print(country_count.loc["CN"])  
"""
Brand             2734
Store Number      2734
Store Name        2734
Ownership Type    2734
Street Address    2734
City              2734
State/Province    2734
Postcode          2192
Phone Number      1337
Timezone          2734
Longitude         2734
Latitude          2734
Name: CN, dtype: int64
"""
```



#### 多条件分组

```python
import pandas as pd

file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)

c = df["Brand"].groupby(by=[df["Country"], df["State/Province"]])  # 这个写成df[]是因为一开始就选定了series对象
print(c.count(),type(c.count()))
c1 = df.groupby(by=["Country", "State/Province"])
print(c1["Brand"].count(),type(c1["Brand"].count()))
# 这两个都是series类型
"""
Country  State/Province
AD       7                  1
AE       AJ                 2
         AZ                48
         DU                82
         FU                 2
                           ..
US       WV                25
         WY                23
VN       HN                 6
         SG                19
ZA       GT                 3
Name: Brand, Length: 545, dtype: int64 <class 'pandas.core.series.Series'>
"""
```

数据只有最后一列是数据，前两个是索引（复合索引）



因为DateFrame方便操作怎么将上面转换成DataFrame呢？

```python
import pandas as pd

file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)

c = df[["Brand"]].groupby(by=[df["Country"], df["State/Province"]])  # 这个写成df[]是因为一开始就选定了series对象
print(c.count(),type(c.count()))
c1 = df.groupby(by=["Country", "State/Province"])
print(c1[["Brand"]].count(),type(c1[["Brand"]].count()))
# 这两个都是DataFrame类型
"""
                        Brand
Country State/Province       
AD      7                   1
AE      AJ                  2
        AZ                 48
        DU                 82
        FU                  2
...                       ...
US      WV                 25
        WY                 23
VN      HN                  6
        SG                 19
ZA      GT                  3

[545 rows x 1 columns] <class 'pandas.core.frame.DataFrame'>
                        Brand
Country State/Province       
AD      7                   1
AE      AJ                  2
        AZ                 48
        DU                 82
        FU                  2
...                       ...
US      WV                 25
        WY                 23
VN      HN                  6
        SG                 19
ZA      GT                  3

[545 rows x 1 columns] <class 'pandas.core.frame.DataFrame'>
"""
```

和一开始的没有什么太大区别，索引和数据都一样，还是复合索引。

```python
import pandas as pd

file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)

c = df[["Brand"]].groupby(by=[df["Country"], df["State/Province"]])  # 这个写成df[]是因为一开始就选定了series对象
print(c.count().index)
c1 = df.groupby(by=["Country", "State/Province"])
print(c1[["Brand"]].count().index)
"""
MultiIndex([('AD',  '7'),
            ('AE', 'AJ'),
            ('AE', 'AZ'),
            ('AE', 'DU'),
            ('AE', 'FU'),
            ('AE', 'RK'),
            ('AE', 'SH'),
            ('AE', 'UQ'),
            ('AR',  'B'),
            ('AR',  'C'),
            ...
            ('US', 'UT'),
            ('US', 'VA'),
            ('US', 'VT'),
            ('US', 'WA'),
            ('US', 'WI'),
            ('US', 'WV'),
            ('US', 'WY'),
            ('VN', 'HN'),
            ('VN', 'SG'),
            ('ZA', 'GT')],
           names=['Country', 'State/Province'], length=545)
MultiIndex([('AD',  '7'),
            ('AE', 'AJ'),
            ('AE', 'AZ'),
            ('AE', 'DU'),
            ('AE', 'FU'),
            ('AE', 'RK'),
            ('AE', 'SH'),
            ('AE', 'UQ'),
            ('AR',  'B'),
            ('AR',  'C'),
            ...
            ('US', 'UT'),
            ('US', 'VA'),
            ('US', 'VT'),
            ('US', 'WA'),
            ('US', 'WI'),
            ('US', 'WV'),
            ('US', 'WY'),
            ('VN', 'HN'),
            ('VN', 'SG'),
            ('ZA', 'GT')],
           names=['Country', 'State/Province'], length=545)
"""
```



## 索引和复合索引

```python
import numpy as np
import pandas as pd

df1 = pd.DataFrame(np.arange(1, 10).reshape(3, 3), index=list('ABC'), columns=list('xyz'))
print(df1.index[1:3])  # Index(['B', 'C'], dtype='object')
df1.index = ['m', 'x', 'n']
print(df1)  # Index(['B', 'C'], dtype='object')
"""
   x  y  z
m  1  2  3
x  4  5  6
n  7  8  9
"""
print(df1.reindex(list('amnjh')))  # 去df1找对应的行组成新的DateFrame，没有行的元素全为nan
"""
     x    y    z
a  NaN  NaN  NaN
m  1.0  2.0  3.0
n  7.0  8.0  9.0
j  NaN  NaN  NaN
h  NaN  NaN  NaN
"""
df2 = df1.set_index('z', drop=True)  # 将某一列设置为索引,drop默认是True，将选择做索引的列的那行数据删除
print(df2, '\n', df2.index)
"""
   x  y
z      
3  1  2
6  4  5
9  7  8 
 Index([3, 6, 9], dtype='int64', name='z')
"""
df3 = df1.set_index('z', drop=False)  # 将某一列设置为索引，drop=False将选择做索引的列的那行数据保留
print(df3, '\n', df3.index)
"""
   x  y  z
z         
3  1  2  3
6  4  5  6
9  7  8  9 
 Index([3, 6, 9], dtype='int64', name='z')
"""
# 这个索引重复，所以可以使用unique函数
print(df2.index.unique())  # Index([3, 6, 9], dtype='int64', name='z')

df4 = df1.set_index(['x', 'y', 'z'], drop=False)

print(df4, '\n', df4.index)
"""
       x  y  z
x y z         
1 2 3  1  2  3
4 5 6  4  5  6
7 8 9  7  8  9 
 MultiIndex([(1, 2, 3),
            (4, 5, 6),
            (7, 8, 9)],
           names=['x', 'y', 'z'])
"""
```



### 复合索引的取值

#### DataFrame取值

```python
import pandas as pd

a = pd.DataFrame(
    {'a': range(7), "b": range(7, 0, -1), "c": ["one", "one", "one", "two", "two", "two", "two"], "d": list("hjklmno")})
print(a)
"""
   a  b    c  d
0  0  7  one  h
1  1  6  one  j
2  2  5  one  k
3  3  4  two  l
4  4  3  two  m
5  5  2  two  n
6  6  1  two  o
"""
b = a.set_index(['c', 'd'])
print(b)
"""
       a  b
c   d      
one h  0  7
    j  1  6
    k  2  5
two l  3  4
    m  4  3
    n  5  2
    o  6  1
       a  b
"""
d = b.swaplevel()
print(d)
"""
d c        
h one  0  7
j one  1  6
k one  2  5
l two  3  4
m two  4  3
n two  5  2
o two  6  1
"""
print(b.loc["one"].loc["h"])
"""
a    0
b    7
Name: h, dtype: int64
"""

print(b.swaplevel().loc["h"])  # 对内层的索引进行查找先将内层索引变成外层索引
"""
     a  b
c        
one  0  7
"""
```



#### series取值

```python
import pandas as pd

a = pd.DataFrame(
    {'a': range(7), "b": range(7, 0, -1), "c": ["one", "one", "one", "two", "two", "two", "two"], "d": list("hjklmno")})
print(a)
"""
   a  b    c  d
0  0  7  one  h
1  1  6  one  j
2  2  5  one  k
3  3  4  two  l
4  4  3  two  m
5  5  2  two  n
6  6  1  two  o
"""
b = a.set_index(['c', 'd'])
print(b)
"""
       a  b
c   d      
one h  0  7
    j  1  6
    k  2  5
two l  3  4
    m  4  3
    n  5  2
    o  6  1
       a  b
"""

print(b["a"]["one","h"])  # print(b["b"]["one"]["h"]) 效果一样，series才可以
```





#### 案例

店铺总数前10的地区

```python
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)

c1 = df.groupby(by=["Country", "State/Province"])[["Brand"]].count()

data = c1.iloc[:10].sort_values(by="Brand", ascending=False)
_x = data.index
_y = np.array(data.values).flatten()  # 如果data是series则直接data.values
plt.figure(figsize=(13,8))
plt.bar(range(len(_x)), _y)
plt.xticks(range(len(_x)),_x)
plt.show()
```

![image-20241005174759572](assets/image-20241005174759572.png)



```python
import pandas as pd
import matplotlib.pyplot as plt

file_path = "./data/starbucks_store_worldwide.csv"
df = pd.read_csv(file_path)
df = df[df["Country"] == "CN"]
# 中国店铺总数排名前10的城市
data = df.groupby(by="City")["Brand"].count().sort_values(ascending=False)[:50]

_x = data.index
_y = data.values
plt.rcParams['font.family'] = 'SimHei'
plt.rcParams['axes.unicode_minus'] = False
plt.figure(figsize=(30, 12),dpi=80)
plt.bar(range(len(_x)), _y, width=0.6, color="red")
plt.xticks(range(len(_x)), _x)
plt.show()

plt.figure(figsize=(20, 12),dpi=80)
plt.barh(range(len(_x)), _y, height=0.5, color="red")
plt.yticks(range(len(_x)), _x)
plt.show()
```

![image-20241005215326628](assets/image-20241005215326628.png)



![image-20241005215333516](assets/image-20241005215333516.png)



# 时间戳



## 用法

**date_range()**

```python
pandas.date_range(
    start=None, 
    end=None, 
    periods=None, 
    freq=None, 
    tz=None, 
    normalize=False, 
    name=None, 
    inclusive='both', 
    *, 
    unit=None, 
    **kwargs
)
```

**参数**：

1. **start**:

   - **类型**: `str`, `datetime-like`
   - **意义**: 序列的起始日期。如果未指定 `end` 或 `periods`，则必须提供。

2. **end**:

   - **类型**: `str`, `datetime-like`
   - **意义**: 序列的结束日期。如果未指定 `start` 或 `periods`，则必须提供。

3. **periods**:

   - **类型**: `int`, `None`
   - **意义**: 要生成的时间点数量。如果指定了 `periods`，则 `start` 或 `end` 其中之一必须指定。否则，日期将按 `start` 和 `end` 的跨度来计算。

4. **freq**:

   - **类型**: `str`, `DateOffset`
   - **意义**: 日期间隔频率。常见选项包括

   | 别名 | 描述                                  |
   | ---- | ------------------------------------- |
   | B    | 工作日频率                            |
   | C    | 自定义工作日频率                      |
   | D    | 自然日频率                            |
   | W    | 每周频率                              |
   | ME   | 月末频率                              |
   | SME  | 半月结束频率（15 日和月底）           |
   | BME  | 营业月结束频率                        |
   | CBME | 自定义 Business Moon End Frequency    |
   | MS   | 月份开始频率                          |
   | SMS  | 半月开始频率（1 日和 15 日）          |
   | BMS  | 营业月开始频率                        |
   | CBMS | 自定义 Business Month Start Frequency |
   | QE   | 季度结束频率                          |
   | BQE  | 业务季度结束频率                      |
   | QS   | 季度开始频率                          |
   | BQS  | 业务季度开始频率                      |
   | YE   | 年终频率                              |
   | BYE  | 业务年度结束频率                      |
   | YS   | Year Start 频率                       |
   | BYS  | 营业年度开始频率                      |
   | h    | 每小时频率                            |
   | bh   | 营业时间频率                          |
   | cbh  | 自定义营业时间频率                    |
   | min  | 分钟频率                              |
   | s    | 第二频率                              |
   | MS   | 毫秒                                  |
   | us   | 微秒                                  |
   | ns   | 纳 秒                                 |

5. **tz**:

   - **类型**: `str` 或 `pytz.timezone` 对象
   - **意义**: 指定时区。可以使用字符串（例如 `'Asia/Shanghai'`）或 `pytz` 模块中的时区对象。

6. **normalize**:

   - **类型**: `bool`
   - **默认值**: `False`
   - **意义**: 如果为 `True`，则将所有日期时间标准化到午夜，即时间部分将被忽略。

7. **name**:

   - **类型**: `str`
   - **意义**: 为生成的 `DatetimeIndex` 对象指定名称。

8. **inclusive**:

   - **类型**: `str`
   - **默认值**: `'both'`
   - **意义**: 指定生成日期范围的包容性，可以是 `'both'`（包括起始和结束日期）、`'neither'`（不包括起始和结束日期）、`'left'`（包括起始日期）、`'right'`（包括结束日期）。

9. **unit**:

   - **类型**: `str` 或 `None`
   - **意义**: 指定输入的单位（例如 `'ms'` 表示毫秒）。仅在 `start` 和 `end` 为整数时使用。

10. **kwargs**:

    - **意义**: 其他附加参数。



**返回对象**

- **类型**: `pandas.DatetimeIndex`
- **意义**: 返回一个 `DatetimeIndex` 对象，包含了生成的日期序列



**适用范围**

- **具体时间索引**：`DatetimeIndex` 表示具体的日期和时间点（如 2022-01-01 00:00:00）。
- **时间点**：`DatetimeIndex` 可以表示任意具体时间点，而不局限于特定的时间段。
- **应用**：通常用于处理各种类型的时间序列数据，例如天气数据、传感器数据等。



**例子**

```python
import pandas as pd

datetime1 = pd.date_range(start="2004-12-12", end="2005-12", freq="ME")
print(datetime1)
"""
DatetimeIndex(['2004-12-31', '2005-01-31', '2005-02-28', '2005-03-31',
               '2005-04-30', '2005-05-31', '2005-06-30', '2005-07-31',
               '2005-08-31', '2005-09-30', '2005-10-31', '2005-11-30'],
              dtype='datetime64[ns]', freq='ME')
"""
datetime2 = pd.date_range(start="2004-12-12", periods=10, freq="SME")
print(datetime2)
"""
DatetimeIndex(['2004-12-15', '2004-12-31', '2005-01-15', '2005-01-31',
               '2005-02-15', '2005-02-28', '2005-03-15', '2005-03-31',
               '2005-04-15', '2005-04-30'],
              dtype='datetime64[ns]', freq='SME-15')
"""
datetime3 = pd.date_range(start="2004-12-12", end="2005-1", freq="3620S")
print(datetime3)
"""
DatetimeIndex(['2004-12-12 10:20:01', '2004-12-12 11:20:21',
               '2004-12-12 12:20:41', '2004-12-12 13:21:01',
               '2004-12-12 14:21:21', '2004-12-12 15:21:41',
               '2004-12-12 16:22:01', '2004-12-12 17:22:21',
               '2004-12-12 18:22:41', '2004-12-12 19:23:01',
               ...
               '2004-12-31 14:52:41', '2004-12-31 15:53:01',
               '2004-12-31 16:53:21', '2004-12-31 17:53:41',
               '2004-12-31 18:54:01', '2004-12-31 19:54:21',
               '2004-12-31 20:54:41', '2004-12-31 21:55:01',
               '2004-12-31 22:55:21', '2004-12-31 23:55:41'],
              dtype='datetime64[ns]', length=468, freq='3620s')
"""
```



## 在DataFrame使用

### **to_datetime()**

可以把不同形式的数据统一转换为`pandas`中的日期时间格式

```python
pandas.to_datetime（arg，errors ='raise'，utc = None，format = None，unit = None ）
```



#### 参数

| 参数   |                             意义                             |
| ------ | :----------------------------------------------------------: |
| errors | 三种取值，‘ignore’, ‘raise’, ‘coerce’，默认为raise。'raise'，则无效的解析将引发异常'coerce'，那么无效解析将被设置为NaT'ignore'，那么无效的解析将返回输入值 |
| utc    |          布尔值，默认为none。返回utc即协调世界时。           |
| format |                    格式化显示时间的格式。                    |
| unit   |          默认值为‘ns’，则将会精确到微妙，‘s'为秒。           |

format参数大部分情况下可以不用写，但是对应pandas无法格式化的时间字符串，我们可以使用该参数，比如包含中文

- %y ：    两位数的年份表示（00-99）
- %Y ：    四位数的年份表示（000-9999）
- %m  ：  月份（01-12）
- %d ：   月内中的一天（0-31）
- %H ：  24小时制小时数（0-23）
- %I  ：   12小时制小时数（01-12）
- %M ：    分钟数（00=59）
- %S  ：   秒（00-59）
- %a  ：   本地简化星期名称
- %A  ：   本地完整星期名称
- %b  ：   本地简化的月份名称
- %B  ：   本地完整的月份名称
- %c  ：   本地相应的日期表示和时间表示
- %j   ：  年内的一天（001-366）
- %p  ：   本地A.M.或P.M.的等价符
- %U  ：   一年中的星期数（00-53）星期天为星期的开始
- %w  ：   星期（0-6），星期天为星期的开始
- %W  ：   一年中的星期数（00-53）星期一为星期的开始
- %x   ：  本地相应的日期表示
- %X  ：   本地相应的时间表示
- %Z  ：   当前时区的名称
- %%  ：   %号本身

---



### resample()

可以进行数据的**重采样**

**重采样**：指的是将时间序列从一个频率转化为另一个频率进行处理的过程，将高频频率数据转化为低频频率为**降采样**，低频频率转化为高频率为**升采样**

resample的方法帮助实现频率转化

```python
DataFrame.resample(rule, how=None, axis=0, fill_method=None, closed=None, label=None, convention=’start’, kind=None, loffset=None, limit=None, base=0, on=None, level=None)
```



#### 参数

- 第一个参数是时间频率字符串，用于指定重新采样的目标频率。常见的选项包括 'D'（每日）、'M'（每月）、'Q'（每季度）、'Y'（每年）等。
- 你可以通过第二个参数how来指定聚合函数，例如 'sum'、'mean'、'max' 等，默认是 'mean'。
- 你还可以使用closed参数来指定每个区间的闭合端点，可选的值包括 'right'、'left'、'both'、'neither'，默认是 'right'。
- 使用label参数来指定重新采样后的标签使用哪个时间戳，可选的值包括 'right'、'left'、'both'、'neither'，默认是 'right'。
- 可以使用loffset参数来调整重新采样后的时间标签的偏移量。
- 最后，你可以使用聚合函数的特定参数，例如'sum'函数的min_count参数来指定非NA值的最小数量。

---



### 例子

统计出911数据中不同月份电话次数的

```python
import pandas as pd
import matplotlib.pyplot as plt

file_path = "./data/911.csv"
df = pd.read_csv(file_path)
# pd.set_option('display.max_columns', None)
df["timeStamp"] = pd.to_datetime(df["timeStamp"])
df.set_index("timeStamp", inplace=True)

# 统计出911数据中不同月份电话次数的
x = df.resample("ME")  # 将所有时间分成不同组，分组按照传入的参数

count_by_month = x.count()["title"]

_x = count_by_month.index
_y = count_by_month.values
# for i in _x:
#     print(dir(i))  # dir展示对象的属性和方法
#     break
_x = [i.strftime("%Y-%m-%d") for i in _x]

plt.figure(figsize=(20,8))
plt.plot(_x, _y)
plt.xticks(rotation=30)
plt.show()
```

![image-20241007124301137](assets/image-20241007124301137.png)



统计911不同危险等级在不同时间段的次数

```python
import pandas as pd
import matplotlib.pyplot as plt

file_path = "./data/911.csv"
df = pd.read_csv(file_path)
pd.set_option('display.max_columns', None)
df["timeStamp"] = pd.to_datetime(df["timeStamp"])

temp_list = df["title"].str.split(": ", expand=True)  # expand是将分开的字符串各位一组
df["dx"] = temp_list[0]
df.set_index("timeStamp", inplace=True)
data = df.groupby("dx")
# print(data)
plt.figure(figsize=(20,8),dpi=80)
for group_name, group_data in data:
    c_b_m = group_data.resample("ME").count()["title"]
    _x = c_b_m.index
    _y = c_b_m.values
    _x = [i.strftime("%Y.%m.%d") for i in _x]
    plt.plot(_x, _y, label=group_name)

plt.legend(loc="best")
plt.xticks(rotation=45)
plt.show()
```

![image-20241007134529656](assets/image-20241007134529656.png)

 

# 时间段

PeriodIndex类型可以理解为时间段

## 用法

原始数据

```python
import pandas as pd

file_path = "./data/PM2.5/BeijingPM20100101_20151231.csv"
df=pd.read_csv(file_path)
print(df)
"""
          No  year  month  day  hour  ...  TEMP  cbwd    Iws  precipitation  Iprec
0          1  2010      1    1     0  ... -11.0    NW   1.79            0.0    0.0
1          2  2010      1    1     1  ... -12.0    NW   4.92            0.0    0.0
2          3  2010      1    1     2  ... -11.0    NW   6.71            0.0    0.0
3          4  2010      1    1     3  ... -14.0    NW   9.84            0.0    0.0
4          5  2010      1    1     4  ... -12.0    NW  12.97            0.0    0.0
...      ...   ...    ...  ...   ...  ...   ...   ...    ...            ...    ...
52579  52580  2015     12   31    19  ...  -3.0    SE   7.14            0.0    0.0
52580  52581  2015     12   31    20  ...  -2.0    SE   8.03            0.0    0.0
52581  52582  2015     12   31    21  ...  -6.0    NE   0.89            0.0    0.0
52582  52583  2015     12   31    22  ...  -6.0    NE   1.78            0.0    0.0
52583  52584  2015     12   31    23  ...  -6.0    NE   2.67            0.0    0.0
"""
```

使用后 1 

```python
period = pd.PeriodIndex.from_fields(year=df["year"], month=df["month"], day=df["day"],hour=df["hour"],freq="M")
print(period)
"""

[52584 rows x 18 columns]
PeriodIndex(['2010-01', '2010-01', '2010-01', '2010-01', '2010-01', '2010-01',
             '2010-01', '2010-01', '2010-01', '2010-01',
             ...
             '2015-12', '2015-12', '2015-12', '2015-12', '2015-12', '2015-12',
             '2015-12', '2015-12', '2015-12', '2015-12'],
            dtype='period[M]', length=52584)
"""
```



使用后 2 

```python
period = pd.PeriodIndex.from_fields(year=df["year"], month=df["month"], day=df["day"],hour=df["hour"],freq="h")
print(period)
"""
PeriodIndex(['2010-01-01 00:00', '2010-01-01 01:00', '2010-01-01 02:00',
             '2010-01-01 03:00', '2010-01-01 04:00', '2010-01-01 05:00',
             '2010-01-01 06:00', '2010-01-01 07:00', '2010-01-01 08:00',
             '2010-01-01 09:00',
             ...
             '2015-12-31 14:00', '2015-12-31 15:00', '2015-12-31 16:00',
             '2015-12-31 17:00', '2015-12-31 18:00', '2015-12-31 19:00',
             '2015-12-31 20:00', '2015-12-31 21:00', '2015-12-31 22:00',
             '2015-12-31 23:00'],
            dtype='period[h]', length=52584)
"""
```

**适用范围**

- **时间段索引**：`PeriodIndex` 表示一段时间（如一周、一月、一季度、一年等）内的所有时间点，而不是具体的日期和时间。
- **频率**：`PeriodIndex` 有固定的频率（如每年、每季度、每月等），用于表示时间段的长度。
- **应用**：通常用于金融数据、季度数据等，需要聚合或处理特定时间段内数据的场景。



## 案例

方法1 

```python
import pandas as pd
from matplotlib import pyplot as plt

file_path = "./data/PM2.5/BeijingPM20100101_20151231.csv"
df=pd.read_csv(file_path)
# 把分开的时间字符串通过periodIndex的方法转化为pandas的时间类型
period = pd.PeriodIndex.from_fields(year=df["year"], month=df["month"], day=df["day"],hour=df["hour"],freq="D")
# print(period)
df["datetime"] = period.to_timestamp()  # 将periodIndex转化为Datetime
df.set_index("datetime", inplace=True)

# 处理缺失数据，直接删除
data = df["PM_US Post"].resample("7D").mean()
_x = data.index
_x = [i.strftime("%Y-%m-%d") for i in _x]
_y = data.values
plt.figure(figsize=(20,8),dpi=80)
plt.plot(range(len(_x)),_y,label="US_PM2.5")
plt.xticks(range(0,len(_x),20),list(_x)[::20],rotation=40)

plt.show()
```

![image-20241007174044048](assets/image-20241007174044048.png)



方法2

```python
import pandas as pd
from matplotlib import pyplot as plt

file_path = "./data/PM2.5/BeijingPM20100101_20151231.csv"
df = pd.read_csv(file_path)
# 把分开的时间字符串通过periodIndex的方法转化为pandas的时间类型
period = pd.PeriodIndex.from_fields(year=df["year"], month=df["month"], day=df["day"], hour=df["hour"], freq="M")
df["datetime"] = period.to_timestamp()  # 将periodIndex转化为Datetime
df.set_index("datetime", inplace=True)
data = df.groupby("datetime")["PM_US Post"].mean()

_x = data.index
_x = [i.strftime("%Y-%m-%d") for i in _x]
_y = data.values
plt.figure(figsize=(20, 8), dpi=80)
plt.plot(range(len(_x)), _y, label="US_PM2.5")
plt.xticks(range(0, len(_x), 20), list(_x)[::20], rotation=40)

plt.show()
```

![image-20241007175254174](assets/image-20241007175254174.png)

方法2不能任意取多少天多少月为一组，不如resample方便，这个通过PeriodIndex一开始的建立直接进行分组。

# 综合案例

## 对911.csv的紧急情况进行统计

```python
import pandas as pd
import numpy as np

file_path = "./data/911.csv"
df = pd.read_csv(file_path)

# 对title进行分析
# 利用pandas进行分析
temp_list = df["title"].str.split(": ", expand=True)  # expand是将分开的字符串各位一组
cate_list = temp_list[0]
print(cate_list.value_counts())
"""
0
EMS        124840
Traffic     87465
Fire        37432
Name: count, dtype: int64
"""

cate_list = cate_list.unique()  # ['EMS' 'Fire' 'Traffic']
# 构造数组
zeros_df = pd.DataFrame(np.zeros((df.shape[0], len(cate_list))), columns=cate_list)
# 利用dateframe对象可以每次处理series对象
for cate in cate_list:
    x = df["title"].str.contains(cate)
    zeros_df.loc[x, cate] = 1
    # zeros_df[cate] = np.where(x, 1, x)  这样写也可以
count = zeros_df.sum()
print(count)
"""
EMS        124844.0
Fire        37432.0
Traffic     87465.0
dtype: float64
"""
# # 死办法
# for i in range(df.shape[0]):
#     zeros_df.loc[i, temp_list[i][0]]=1
# print(zeros_df)
# # 结果直接报错，太多了，阻断运行
```

---



```python
import pandas as pd

file_path = "./data/911.csv"
df = pd.read_csv(file_path)

# 对title进行分析
# 利用pandas进行分析
temp_list = df["title"].str.split(": ", expand=True)  # expand是将分开的字符串各位一组
df["dx"] = temp_list[0]
print(df.head())
"""
         lat        lng  ...  e    dx
0  40.297876 -75.581294  ...  1   EMS
1  40.258061 -75.264680  ...  1   EMS
2  40.121182 -75.351975  ...  1  Fire
3  40.116153 -75.343513  ...  1   EMS
4  40.251492 -75.603350  ...  1   EMS

[5 rows x 10 columns]
"""
print(df.groupby(by="dx")["title"].count())
"""
dx
EMS        124840
Fire        37432
Traffic     87465
Name: title, dtype: int64
"""
```



# 打印数据的多少

pycharm打印DataFrame类型会进行省略，添加下面代码可以自由设置打印多少。

```python
import pandas as pd
import numpy as np
 
#pandas设置最大显示行和列
pd.set_option('display.max_columns',50)
pd.set_option('display.max_rows',300)
 
#调整显示宽度，以便整行显示
pd.set_option('display.width',1000) 
 
#显示所有列
pd.set_option('display.max_columns', None)
#显示所有行
pd.set_option('display.max_rows', None)
#设置value的显示长度为100，默认为50
pd.set_option('max_colwidth',100)
```







# 常用函数



## value_counts()

```python
value_counts(values,sort=True, ascending=False, normalize=False,bins=None,dropna=True)
```

**sort=True**： 是否要进行排序；默认进行排序
**ascending=False：** 默认降序排列；
**normalize=False**： 是否要对计算结果进行标准化并显示标准化后的结果，默认是False。
**bins=None：** 可以自定义分组区间，默认是否；
**dropna=True：**是否删除缺失值nan，默认删除

```python
import pandas as pd

file_path = "./data/IMDB-Movie-Data.csv"
df = pd.read_csv(file_path)
# 将每部电影的类型组装成列表
tempe_list = df["Genre"].value_counts()
print(tempe_list)
"""
Genre
Action,Adventure,Sci-Fi     50
Drama                       48
Comedy,Drama,Romance        35
Comedy                      32
Drama,Romance               31
                            ..
Drama,Fantasy,Musical        1
Adventure,Family             1
Adventure,Comedy,Fantasy     1
Drama,Family,Music           1
Comedy,Family,Fantasy        1
Name: count, Length: 207, dtype: int64
"""
```



## sort_values

```python
import pandas as pd
df = pd.read_csv("./data/dogNames2.csv")

df = df.sort_values(by="Count_AnimalName",ascending=False)  # 通过by选择筛选对象,默认升序

print(df.head())
"""
      Row_Labels  Count_AnimalName
1156       BELLA              1195
9140         MAX              1153
2660     CHARLIE               856
3251        COCO               852
12368      ROCKY               823
"""
```



## loc

df.loc通过标签索引取数据

![image-20240926200548920](assets/image-20240926200548920.png)



## iloc

df.iloc()和numpy取法类似

下面的部分略有不同

```python
import numpy as np
import pandas as pd

t3 = pd.DataFrame(np.arange(12).reshape(3,4),index=list("abc"),columns=list('wxyz'))
t1 = np.arange(12).reshape(3,4)
print(t1)
print(t1[[0,2],[0,1]],"\n")

print(t3)
print(t3.iloc[[0,2],[0,1]])
"""
[[ 0  1  2  3]
 [ 4  5  6  7]
 [ 8  9 10 11]]
[0 9] 

   w  x   y   z
a  0  1   2   3
b  4  5   6   7
c  8  9  10  11
   w  x
a  0  1
c  8  9
"""
```







## tolist()

```python
import pandas as pd

df = pd.read_csv("./data/dogNames2.csv")

df = df.sort_values(by="Count_AnimalName", ascending=False)  # 通过by选择筛选对象,默认升序

print(df[(df["Count_AnimalName"] > 700)])  # 找到取名字次数大于700的个数，输出出来

# tolist()函数只能对Series对象操作，我们只要名字因此只取Row_Labels列
print(df[df["Count_AnimalName"] > 700]["Row_Labels"].tolist())
"""
      Row_Labels  Count_AnimalName
1156       BELLA              1195
9140         MAX              1153
2660     CHARLIE               856
3251        COCO               852
12368      ROCKY               823
8417        LOLA               795
8552       LUCKY               723
8560        LUCY               710
['BELLA', 'MAX', 'CHARLIE', 'COCO', 'ROCKY', 'LOLA', 'LUCKY', 'LUCY']
"""
```





## swaplevel()

```python
DataFrame.swaplevel(i=- 2, j=- 1, axis=0)
```

在 **MultiIndex**中交换级别 i 和 j。

默认是交换索引的两个最内层。



### 参数：

- **i, j**：整数或字符串

  要交换的索引的级别。可以将级别名称作为字符串传递。

- **axis**：{0 或 ‘index’，1 或 ‘columns’}，默认 0

  交换级别的轴。 0 或 ‘index’ 表示按行，1 或 ‘columns’ 表示按列。

### 返回：

- DataFrame

  在 MultiIndex 中交换级别的 DataFrame。



```python
df = pd.DataFrame({"Grade":["A", "B", "A", "C"]},
    index=[
        ["Final exam", "Final exam", "Coursework", "Coursework"],
        ["History", "Geography", "History", "Geography"],
        ["January", "February", "March", "April"],
    ],
)
print(df)
"""
                                    Grade
Final exam  History     January      A
            Geography   February     B
Coursework  History     March        A
            Geography   April        C
"""

print(df.swaplevel())
"""
                                    Grade
Final exam  January     History         A
            February    Geography       B
Coursework  March       History         A
            April       Geography       C
"""

```

通过提供一个参数，我们可以选择与哪个索引交换最后一个索引。例如，我们可以将第一个索引与最后一个索引交换如下。

```python
df.swaplevel(0)
"""
                                    Grade
January     History     Final exam      A
February    Geography   Final exam      B
March       History     Coursework      A
April       Geography   Coursework      C
"""
```

我们还可以通过为 i 和 j 提供值来明确定义要交换的索引。在这里，我们例如交换第一个和第二个索引。

```python
df.swaplevel(0, 1)
"""
                                    Grade
History     Final exam  January         A
Geography   Final exam  February        B
History     Coursework  March           A
Geography   Coursework  April           C
"""
```



## iterrows()

Pandas的基础数据结构可以分为两种：DataFrame和Series。不同于Series的是，Dataframe不仅有行索引还有列索引 。df.iterrows( )函数：可以返回所有的行索引，以及该行的所有内容



```python
import pandas as pd
import matplotlib.pyplot as plt

file_path = "./data/911.csv"
df = pd.read_csv(file_path)
# pd.set_option('display.max_columns', None)
df["timeStamp"] = pd.to_datetime(df["timeStamp"])
df.set_index("timeStamp", inplace=True)

# 统计出911数据中不同月份电话次数的
x = df.resample("ME").count()  # 将所有时间分成不同组，分组按照传入的参数
print(x)
for index,row in x.iterrows():
    print(index)
    print(row)
    break
"""
              lat    lng   desc    zip  title    twp   addr      e
timeStamp                                                         
2015-12-31   7916   7916   7916   6902   7916   7911   7916   7916
2016-01-31  13096  13096  13096  11512  13096  13094  13096  13096
2016-02-29  11396  11396  11396   9926  11396  11395  11396  11396
2016-03-31  11059  11059  11059   9754  11059  11052  11059  11059
2016-04-30  11287  11287  11287   9897  11287  11284  11287  11287
2016-05-31  11374  11374  11374   9938  11374  11371  11374  11374
2016-06-30  11732  11732  11732  10205  11732  11726  11732  11732
2016-07-31  12088  12088  12088  10626  12088  12086  12088  12088
2016-08-31  11904  11904  11904  10381  11904  11902  11904  11904
2016-09-30  11669  11669  11669  10174  11669  11666  11669  11669
2016-10-31  12502  12502  12502  10760  12502  12499  12502  12502
2016-11-30  12091  12091  12091  10559  12091  12086  12091  12091
2016-12-31  12162  12162  12162  10763  12162  12156  12162  12162
2017-01-31  11605  11605  11605  10365  11605  11598  11605  11605
2017-02-28  10267  10267  10267   9235  10267  10263  10267  10267
2017-03-31  11684  11684  11684  10406  11684  11680  11684  11684
2017-04-30  11056  11056  11056   9774  11056  11052  11056  11056
2017-05-31  11719  11719  11719  10316  11719  11711  11719  11719
2017-06-30  12333  12333  12333  10865  12333  12332  12333  12333
2017-07-31  11768  11768  11768  10314  11768  11764  11768  11768
2017-08-31  11753  11753  11753  10358  11753  11744  11753  11753
2017-09-30   7276   7276   7276   6361   7276   7272   7276   7276
2015-12-31 00:00:00
lat      7916
lng      7916
desc     7916
zip      6902
title    7916
twp      7911
addr     7916
e        7916
Name: 2015-12-31 00:00:00, dtype: int64
"""
```

iterrows()函数可以提取每一行的下标和内容





## agg()

`agg`是``aggregate`的别名

**DataFrame.aggregate(func=None, axis=0, args,kwargs)**

| 参数名 | 解释                                                         | 传参格式                          | 例如             |
| ------ | ------------------------------------------------------------ | --------------------------------- | ---------------- |
| func   | 用于汇总数据的功能。如果是函数，则必须在传递DataFrame或传递给DataFrame.apply时起作用。 | 函数，str，列表或字典             | [np.sum, 'mean'] |
| axis   | 如果为0或'index'：将函数应用于每一列。如果为1或“列”：将函数应用于每一行。 | {0或'index'，1或'columns'}，默认0 | 1                |

它会return的数据类型一般为：标量（值）、Series、DataFrame三种。

对应可以使用

- 标量：使用单个函数调用Series.agg
  Series：使用单个函数调用DataFrame.agg
  DaFrame:使用多个函数调用DataFrame.agg



### 返回例子

#### 标量

```python
import pandas as pd

s_df = pd.Series([1, 2, 3])
print(s_df)
print(s_df.agg('sum'))
"""
0    1
1    2
2    3
dtype: int64
6
"""
```



### **Seires**

```python
import numpy as np
import pandas as pd
df = pd.DataFrame([[1, 2, 3],
                   [4, 5, 6],
                   [7, 8, 9],
                   [np.nan, np.nan, np.nan]],
                  columns=['A', 'B', 'C'])
print(df)
"""
     A    B    C
0  1.0  2.0  3.0
1  4.0  5.0  6.0
2  7.0  8.0  9.0
3  NaN  NaN  NaN
"""
print(df.agg('sum'))
"""
A    12.0
B    15.0
C    18.0
dtype: float64
"""
```



### **DataFrame**

```python
import numpy as np
import pandas as pd

df = pd.DataFrame([[1, 2, 3],
                   [4, 5, 6],
                   [7, 8, 9],
                   [np.nan, np.nan, np.nan]],
                  columns=['A', 'B', 'C'])
print(df)
"""
     A    B    C
0  1.0  2.0  3.0
1  4.0  5.0  6.0
2  7.0  8.0  9.0
3  NaN  NaN  NaN
"""

# 在行上汇总这些功能
print(df.agg(['sum', 'min']))
"""
       A     B     C
 sum  12.0  15.0  18.0
 min   1.0   2.0   3.0
"""

# 每列不同的聚合
print(df.agg({'A': ['sum', 'min'], 'B': ['min', 'max']}))
"""
        A    B
sum  12.0  NaN
min   1.0  2.0
max   NaN  8.0
"""

# 在列上聚合不同的函数，然后重命名结果DataFrame的索引
print(df.agg(x=('A', 'max'), y=('B', 'min'), z=('C', 'mean')))
"""
     A    B    C
x  7.0  NaN  NaN
y  NaN  2.0  NaN
z  NaN  NaN  6.0
"""
```



### **GroupBy的agg用法案例**

数据构造

```python
import pandas as pd

df = pd.DataFrame({'Country': ['China', 'China', 'India', 'India', 'America', 'Japan', 'China', 'India'],
                   'Income': [10000, 10000, 5000, 5002, 40000, 50000, 8000, 5000],
                   'Age': [5000, 4321, 1234, 4010, 250, 250, 4500, 4321]})
print(df)
"""
   Country  Income   Age
0    China   10000  5000
1    China   10000  4321
2    India    5000  1234
3    India    5002  4010
4  America   40000   250
5    Japan   50000   250
6    China    8000  4500
7    India    5000  4321
"""
```



 接下来会按照城市分组，用print()方法给认知groupby的分组逻辑

```python
import pandas as pd

df = pd.DataFrame({'Country': ['China', 'China', 'India', 'India', 'America', 'Japan', 'China', 'India'],
                   'Income': [10000, 10000, 5000, 5002, 40000, 50000, 8000, 5000],
                   'Age': [5000, 4321, 1234, 4010, 250, 250, 4500, 4321]})

# 接下来会按照城市分组，用print()方法给认知groupby的分组逻辑
df.groupby(['Country']).apply(lambda x: print(x,type(x)),include_groups=False)
"""
   Income  Age
4   40000  250 <class 'pandas.core.frame.DataFrame'>
   Income   Age
0   10000  5000
1   10000  4321
6    8000  4500 <class 'pandas.core.frame.DataFrame'>
   Income   Age
2    5000  1234
3    5002  4010
7    5000  4321 <class 'pandas.core.frame.DataFrame'>
   Income  Age
5   50000  250 <class 'pandas.core.frame.DataFrame'>
"""
```

这儿其实就很清晰了，分组里面的结果就是一个个分组后的`DataFrame`。所以针对Groupby后agg的用法，就是DataFrame.agg的用法，不用额外说什么，照样是 **列表、字典** 形式传入。

#### 列表传参

```python
import pandas as pd

df = pd.DataFrame({'Country': ['China', 'China', 'India', 'India', 'America', 'Japan', 'China', 'India'],
                   'Income': [10000, 10000, 5000, 5002, 40000, 50000, 8000, 5000],
                   'Age': [5000, 4321, 1234, 4010, 250, 250, 4500, 4321]})

df_agg = df.groupby('Country').agg(['min', 'mean', 'max'])
print(df_agg)
"""
        Income                        Age                   
           min          mean    max   min         mean   max
Country                                                     
America  40000  40000.000000  40000   250   250.000000   250
China     8000   9333.333333  10000  4321  4607.000000  5000
India     5000   5000.666667   5002  1234  3188.333333  4321
Japan    50000  50000.000000  50000   250   250.000000   250
"""
```



#### 字典传参

```python
import pandas as pd

df = pd.DataFrame({'Country': ['China', 'China', 'India', 'India', 'America', 'Japan', 'China', 'India'],
                   'Income': [10000, 10000, 5000, 5002, 40000, 50000, 8000, 5000],
                   'Age': [5000, 4321, 1234, 4010, 250, 250, 4500, 4321]})

print(df.groupby('Country').agg({'Age': ['min', 'mean', 'max'], 'Income': ['min', 'max']}))
"""
          Age                    Income       
          min         mean   max    min    max
Country                                       
America   250   250.000000   250  40000  40000
China    4321  4607.000000  5000   8000  10000
India    1234  3188.333333  4321   5000   5002
Japan     250   250.000000   250  50000  50000
"""
```

总结：首先了解`agg`能传什么形式的**func**，再清晰`groupby`的形式，就知道`groupy+agg`结合起来的用法。



agg其实就是调用apply函数，也就是apply函数能用的它也能用





## apply



### 1.基本信息

 Pandas 的 `apply()` 方法是用来调用一个函数(Python method)，让此函数对数据对象进行批量处理。Pandas 的很多对象都可以使用 `apply()` 来调用函数，如 **Dataframe**、**Series**、分组对象、各种**时间序列**等。



### 2.语法结构

 `apply()` 使用时，通常放入一个 `lambda` 函数表达式、或一个函数作为操作运算，官方上给出DataFrame的 `apply()` 用法：

```python
DataFrame.apply(self, func, axis=0, raw=False, result_type=None, args=(), **kwargs)
```



**参数：**

- func：函数或 lambda 表达式,应用于每行或者每列

- axis：{0 or ‘index’, 1 or ‘columns’}, 默认为0
  - 0 or ‘index’: 表示函数处理的是每一列
  - 1 or ‘columns’: 表示函数处理的是每一行

- raw：bool 类型，默认为 False;
  - False ，表示把每一行或列作为 Series 传入函数中；
    True，表示接受的是 ndarray 数据类型；

- result_type：{‘expand’, ‘reduce’, ‘broadcast’, None}, default None

  These only act when axis=1 (columns):

- - ‘expand’ : 列表式的结果将被转化为列。
  - ‘reduce’ : 如果可能的话，返回一个 Series，而不是展开类似列表的结果。这与 expand 相反。
  - ‘broadcast’ : 结果将被广播到 DataFrame 的原始形状，原始索引和列将被保留。

- args: func 的位置参数

- **kwargs：要作为关键字参数传递给 func 的其他关键字参数，1.3.0 开始支持

**返回值：**

- Series 或者 DataFrame：沿数据的给定轴应用 func 的结果
  

```python
Objects passed to the function are Series objects whose index is either the DataFrame's index (``axis=0``) or the DataFrame's columns(``axis=1``). 
传递给函数的对象是Series对象，其索引是DataFrame的索引(axis=0)或DataFrame的列(axis=1)。
By default (``result_type=None``), the final return type is inferred from the return type of the applied function. Otherwise,it depends on the `result_type` argument.
默认情况下( result_type=None)，最终的返回类型是从应用函数的返回类型推断出来的。否则，它取决于' result_type '参数。
```



注：DataFrame与Series的区别与联系：

**区别：**

- series，只是一个一维结构，它由index和value组成。
  dataframe，是一个二维结构，除了拥有index和value之外，还拥有column。

**联系：**

- dataframe由多个series组成，无论是行还是列，单独拆分出来都是一个series。



### 3.使用案例

#### 3.1 DataFrame使用apply

**官方使用案例**

```python
import pandas as pd
import numpy as np

df = pd.DataFrame([[4, 9]] * 3, columns=['A', 'B'])

print(df)
"""
   A  B
0  4  9
1  4  9
2  4  9
"""

# 使用numpy通用函数 (如 np.sqrt(df)),
print(df.apply(np.sqrt))
'''
     A    B
0  2.0  3.0
1  2.0  3.0
2  2.0  3.0
'''

# 使用聚合功能
print(df.apply(np.sum, axis=0))
'''
A    12
B    27
dtype: int64
'''

print(df.apply(np.sum, axis=1))
'''
0    13
1    13
2    13
dtype: int64
'''

# 在每行上返回类似列表的内容
print(df.apply(lambda x: [1, 2], axis=1))
'''
0    [1, 2]
1    [1, 2]
2    [1, 2]
dtype: object
'''

# result_type='expand' 将类似列表的结果扩展到数据的列
print(df.apply(lambda x: [1, 2], axis=1, result_type='expand'))

'''
   0  1
0  1  2
1  1  2
2  1  2
'''

# 在函数中返回一个序列，生成的列名将是序列索引。
print(df.apply(lambda x: pd.Series([1, 2], index=['foo', 'bar']), axis=1))

'''
   foo  bar
0    1    2
1    1    2
2    1    2
'''

# result_type='broadcast' 将确保函数返回相同的形状结果
# 无论是 list-like 还是 scalar，并沿轴进行广播
# 生成的列名将是原始列名。
print(df.apply(lambda x: [1, 2], axis=1, result_type='broadcast'))
'''
A  B
0  1  2
1  1  2
2  1  2
'''
```



**其他案例：**

```python
import pandas as pd
import numpy as np

df = pd.DataFrame({'A': [1, 2, 3],
                   'B': [4, 5, 6],
                   'C': [7, 8, 9]},
                  index=['a', 'b', 'c'])

print(df)
"""
   A  B  C
a  1  4  7
b  2  5  8
c  3  6  9
"""

# 对各列应用函数 axis=0
print(df.apply(lambda x: np.sum(x)))
"""
A     6
B    15
C    24
dtype: int64
"""

# 对各行应用函数
print(df.apply(lambda x: np.sum(x), axis=1))
"""
a    12
b    15
c    18
dtype: int64
"""
```



#### 3.2 Series使用apply

```python
import pandas as pd
import numpy as np

s = pd.Series([20, 21, 12],index=['London', 'New York', 'Helsinki'])
print(s)
'''
London      20
New York    21
Helsinki    12
dtype: int64
'''

# 定义函数并将其作为参数传递给 apply，求值平方化。
def square(x):
     return x ** 2

print(s.apply(square))
'''
London      400
New York    441
Helsinki    144
dtype: int64
'''

# 通过将匿名函数作为参数传递给 apply
print(s.apply(lambda x: x ** 2))
'''
London      400
New York    441
Helsinki    144
dtype: int64
'''

# 定义一个需要附加位置参数的自定义函数
# 并使用args关键字传递这些附加参数。
def subtract_custom_value(x, custom_value):
     return x - custom_value

print(s.apply(subtract_custom_value, args=(5,)))
'''
London      15
New York    16
Helsinki     7
dtype: int64
'''

# 定义一个接受关键字参数并将这些参数传递
# 给 apply 的自定义函数。
def add_custom_values(x, **kwargs):
     for month in kwargs:
         x += kwargs[month]
     return x

print(s.apply(add_custom_values, june=30, july=20, august=25))
'''
London      95
New York    96
Helsinki    87
dtype: int64
'''

# 使用Numpy库中的函数
print(s.apply(np.log))
'''
London      2.995732
New York    3.044522
Helsinki    2.484907
dtype: float64
'''
```





## corr()

Pandas 使用 **corr()** 方法计算数据集中每列之间的关系。

```python
df.corr(method='pearson', min_periods=1)
```

- **method** (可选): 字符串类型，用于指定计算相关系数的方法。默认是 'pearson'，还可以选择 'kendall'（Kendall Tau 相关系数）或 'spearman'（Spearman 秩相关系数）。
  - **Pearson 相关系数:** 即皮尔逊相关系数，用于衡量了两个变量之间的线性关系强度和方向。它的取值范围在 -1 到 1 之间，其中 -1 表示完全负相关，1 表示完全正相关，0 表示无线性相关。可以使用 **corr()** 方法计算数据框中各列之间的 Pearson 相关系数。
  - **Spearman 相关系数：**即斯皮尔曼相关系数，是一种秩相关系数。用于衡量两个变量之间的单调关系，即不一定是线性关系。它通过比较变量的秩次来计算相关性。可以使用 **corr(method='spearman')** 方法计算数据框中各列之间的 Spearman 相关系数。
- **min_periods** (可选): 表示计算相关系数时所需的最小观测值数量。默认值是 1，即只要有至少一个非空值，就会进行计算。如果指定了 `min_periods`，并且在某些列中的非空值数量小于该值，则相应列的相关系数将被设为 NaN。

**df.corr()** 方法返回一个相关系数矩阵，矩阵的行和列对应数据框的列名，矩阵的元素是对应列之间的相关系数。

常见的相关性系数包括 Pearson 相关系数和 Spearman 秩相关系数：



### **Pearson 相关系数**

```python
import pandas as pd

# 创建一个示例数据框
data = {'A': [1, 2, 3, 4, 5], 'B': [5, 4, 3, 2, 1]}
df = pd.DataFrame(data)

# 计算 Pearson 相关系数
correlation_matrix = df.corr()
print(correlation_matrix)
"""
     A    B
A  1.0 -1.0
B -1.0  1.0
"""
```

**说明**：由于数据集是线性相关的，因此 Pearson 相关系数矩阵对角线上的值为 1，而非对角线上的值为 -1 表示完全负相关。



### **Spearman 秩相关系数**

```python
import pandas as pd

# 创建一个示例数据框
data = {'A': [1, 2, 3, 4, 5], 'B': [5, 4, 3, 2, 1]}
df = pd.DataFrame(data)

# 计算 Spearman 相关系数
spearman_correlation_matrix = df.corr(method='spearman')
print(spearman_correlation_matrix)
"""
     A    B
A  1.0 -1.0
B -1.0  1.0
"""
```

**说明：**Spearman 相关系数矩阵的结果与 Pearson 相关系数矩阵相同，因为这两个变量之间是完全的单调负相关。



### **可视化相关性**

这里我们要使用 Python 的 Seaborn 库， Seaborn 是一个基于 Matplotlib 的数据可视化库，专注于统计图形的绘制，旨在简化数据可视化的过程。

Seaborn 提供了一些简单的高级接口，可以轻松地绘制各种统计图形，包括散点图、折线图、柱状图、热图等，而且具有良好的美学效果。

```python
import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd

# 创建一个示例数据框
data = {'A': [1, 2, 3, 4, 5], 'B': [5, 4, 3, 2, 1]}
df = pd.DataFrame(data)

# 计算 Pearson 相关系数
correlation_matrix = df.corr()
# 使用热图可视化 Pearson 相关系数
sns.heatmap(correlation_matrix, annot=True, cmap='coolwarm', fmt=".2f")
plt.show()
```

**说明：**这段代码将生成一个热图，用颜色表示相关系数的强度，其中正相关用温暖色调表示，负相关用冷色调表示。**annot=True** 参数在热图上显示具体的数值。

![img](assets/38fa14c44f0cc4f315a3dd4d5597d2b1_w.png)


## train_test_split()
python机器学习中常用 train_test_split()函数划分训练集和测试集，其用法语法如下：
```python
X_train, X_test, y_train, y_test = train_test_split(train_data, train_target, test_size, random_state, shuffle) 
```
| 返回值  |       描述       |
| ------- | :--------------: |
| X_train | 划分的训练集数据 |
| X_test  | 划分的测试集数据 |
| y_train | 划分的训练集标签 |
| y_test  | 划分的测试集标签 |

| 参数         |                             描述                             |
| ------------ | :----------------------------------------------------------: |
| train_data   |                       还未划分的数据集                       |
| train_target |                        还未划分的标签                        |
| test_size    |       分割比例，默认为0.25，即测试集占完整数据集的比例       |
| random_state | 随机数种子，应用于分割前对数据的洗牌。可以是int，RandomState实例或None，默认值=None。设成定值意味着，对于同一个数据集，只有第一次运行是随机的，随后多次分割只要rondom_state相同，则划分结果也相同 |
| shuffle      |   是否在分割前对完整数据进行洗牌（打乱），默认为True，打乱   |

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
dataset = load_iris()
# print(dataset) 太多了就不看了
X = dataset.data
y = dataset.target
# print(X)
print(y)
"""
[0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
 0 0 0 0 0 0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1
 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 2 2 2 2 2 2 2 2 2 2 2
 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2
 2 2]
"""
X_train, X_test, y_train, y_test = train_test_split(X, y, random_state=1)
print("There are {} samples in the original data".format(y.shape[0]))
print("There are {} training samples".format(y_train.shape[0]))
print("There are {} testing samples".format(y_test.shape[0]))
"""
There are 150 samples in the original data
There are 112 training samples
There are 38 testing samples
"""
m,n=train_test_split(y, test_size=0.2, random_state=1,shuffle=False)
print(m,len(m))
"""
[0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
 0 0 0 0 0 0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1
 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 2 2 2 2 2 2 2 2 2 2 2
 2 2 2 2 2 2 2 2 2] 120
"""
print(n,len(n))
"""
[2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2] 30
"""
```





## drop()

是一种用于从数据框中删除给定坐标轴上的行或列的方法。它可以用于删除指定标签的行或列，并返回新的数据框。

```python
DataFrame.drop(labels=None, axis=0, index=None, columns=None, level=None, inplace=False, errors='raise')
```

在上面的代码中，参数含义如下：

- `labels`：删除的标签。
- `axis`：用于确定要删除的是行还是列，0表示行，1表示列。
- `index`：删除的行索引。
- `columns`：删除的列索引。
- `level`：只适用于具有多层索引的数据帧，指定要删除的级别。
- `inplace`：指定是否在原始数据帧中进行删除操作。默认值为False。
- `errors`：指定如何处理无效标签。如果它们是raise，则引发异常。否则，可以将它们忽略或打印警告信息。



### 单索引

```python
import pandas as pd

data = {'name': ['John', 'Marry', 'Peter', 'David'],
        'age': [22, 31, 36, 28],
        'gender': ['Male', 'Female', 'Male', 'Male'],
        'Marks': [84, 61, 35, 77]}

df = pd.DataFrame(data)

print(df)
"""
    name  age  gender  Marks
0   John   22    Male     84
1  Marry   31  Female     61
2  Peter   36    Male     35
3  David   28    Male     7
"""
df = df.drop([0,3],axis=0)
df = df.drop(['age'],axis=1)

print(df)
"""
    name gender  Marks
1  Marry Female     61
2  Peter   Male     35
"""
```



### 多索引

```python
import pandas as pd

data = {('class 1', 'Alice'): [84, 72, 'M'],
        ('class 1', 'Bob'): [78, 92, 'M'],
        ('class 2', 'Charlie'): [65, 35, 'F'],
        ('class 2', 'David'): [85, 78, 'M']}

df = pd.DataFrame(data, index=['marks', 'age', 'Gender']).T

print(df)
"""
                marks age Gender
class 1 Alice      84  72      M
        Bob        78  92      M
class 2 Charlie    65  35      F
        David      85  78      M
"""
df.drop(index='Bob', level=1, inplace=True)

print(df)
"""
                marks age Gender
class 1 Alice      84  72      M
class 2 Charlie    65  35      F
        David      85  78      M
"""
```



## **get_dummies()**

是用于数据处理的。它将分类数据转换为虚拟变量或指标变量。

在做分类预测的问题的时候，对特征变量的处理是很重要的一个环节。像gender=[“male”, “female”]这种特征值是有限的离散值的变量一般称为分类变量，对分类变量的处理极其重要。

大多数时候，分类变量都是由字符串表示的特征，很多算法都无法直接使用。处理方式一般有两种：一种是用数字为每一个特征值编码，例如前面说的male/female可以使用0和1表示。另一种是利用one hot encoding（独热编码）。也就是用一个长度为特征值数量的向量表示，每一位只有0和1两种取值。本文主要关注one hot encoding的处理。

![img](assets/88e99acd-d2cf-4ea4-9e8f-7e022b74bf2f.png)

上图就是一个示例。本来我们有一个特征Color，取值有三种：Red,Yellow,Green。使用one hot encoding可以将这个特征拆分成三个特征，分别是Red,Yellow,Green。当某个样本的Color特征是一个具体的值的时候，在新的三个特征对应的列取值1，剩下的取值为0即可。

当类别变量下的不同特征值数量比较少的时候，采用one hot encoding是很有效的（特征值数量很多的处理一般不会采用这个方式)

在Python的数据处理和机器学习中，最常使用`pandas.get_dummies`和`sklearn.preprocessing.OneHotEncoder`来做分类变量的one hot encoding。



### 函数

```python
pandas.get_dummies(data, prefix=None, prefix_sep=’_’, dummy_na=False, columns=None, sparse=False, drop_first=False, dtype=None)
```

### 参数

- data：谁的数据要被操作。
- prefix:用于附加DataFrame列名的字符串。在DataFrame上调用get_dummies时，传递一个长度等于列数的列表。默认值为无。
- prefix_sep: 在添加任何前缀时使用的分隔符/分界符。默认为”_”。
- dummy_na: 它增加了一列来表示NaN值，默认值为false，如果false，NaN将被忽略。
- columns:DataFrame中需要编码的列名。默认值是无，如果列是无，那么所有具有对象或类别类型的列都将被转换。
- sparse:它指定假编码列是否应该由SparseArray（True）或普通NumPy数组（False）来支持。默认值为False。
- drop_first: 删除第一层，从k个分类层次中得到k-1个假人。
- dtype: 新列的数据类型。只允许有一个dtype。默认值是np.uint8。

### **返回值:** 

**Dataframe (Dummy-coded data)**



### 例子



#### 示例 1

```python
import pandas as pd

con = pd.Series(list('abcba'))
print(con)
"""
0    a
1    b
2    c
3    b
4    a
dtype: object
"""
print(pd.get_dummies(con))
"""
       a      b      c
0   True  False  False
1  False   True  False
2  False  False   True
3  False   True  False
4   True  False  False
"""
```



#### **示例 2:**

```python
import pandas as pd
import numpy as np

# list
li = ['s', 'a', 't', np.nan]
print(pd.get_dummies(li))
"""
       a      s      t
0  False   True  False
1   True  False  False
2  False  False   True
3  False  False  False
"""
```



#### **示例 3:**

```python
import pandas as pd
import numpy as np

# list
li = ['s', 'a', 't', np.nan]
print(pd.get_dummies(li, dummy_na=True))
"""
       a      s      t    NaN
0  False   True  False  False
1   True  False  False  False
2  False  False   True  False
3  False  False  False   True
"""
```



#### **示例 4_1:**

```python
import pandas as pd
df = pd.DataFrame(
    [
        [1000, "male", 23],
        [1001, "female", 22],
        [1002, "male", 69]
    ],
    columns=['id', 'gender', 'age']
).set_index("id")
# 第一步：使用 get_dummies 编码性别特征
dummy_df = pd.get_dummies(df[["gender"]])
print(dummy_df)
"""
      gender_female  gender_male
id                              
1000          False         True
1001           True        False
1002          False         True
"""
# 第 2 步：将 dummy_df 与原始 DF 连接
df = pd.concat([df, dummy_df], axis=1)
# 第 3 步：删除原始特征
df.drop("gender", axis=1, inplace=True)
print(df)
"""
      age  gender_female  gender_male
id                                   
1000   23          False         True
1001   22           True        False
1002   69          False         True
"""
```



#### 示例 4_2_1:

```python
import pandas as pd
import numpy as np

# dictionary
df = pd.DataFrame({"R":['a','c','d'],
                   'T':['d','a','c'],
                   'S':[1,2,3]})
print(df)
"""
   R  T  S
0  a  d  1
1  c  a  2
2  d  c  3
"""
print(pd.get_dummies(df))
"""
   S    R_a    R_c    R_d    T_a    T_c    T_d
0  1   True  False  False  False  False   True
1  2  False   True  False   True  False  False
2  3  False  False   True  False   True  False
"""
```



#### 示例 4_2_2:

```python
import pandas as pd
import numpy as np

# dictionary
df = pd.DataFrame({"R":['a','c','d'],
                   'T':['d','a','c'],
                   'S_':[1,2,3]})
print(df)
print(pd.get_dummies(df,prefix_sep=['column1', 'column2']))
"""
   R  T  S_
0  a  d   1
1  c  a   2
2  d  c   3
   S_  Rcolumn1a  Rcolumn1c  Rcolumn1d  Tcolumn2a  Tcolumn2c  Tcolumn2d
0   1       True      False      False      False      False       True
1   2      False       True      False       True      False      False
2   3      False      False       True      False       True      False
"""
```



### get_dummies的一个陷阱

准确来说，这不是`pandas.get_dummies`的问题，而是one hot encoding的问题。就是当我们使用回归这种方法的时候，一般来说希望自变量是相互独立的。如果特征变量之间有相关性，模型将很难说出某个变量对目标的影响有多大。在这种情况下，回归模型的系数将无法传达正确的信息。但是使用上面`pandas.get_dummies`来处理就会引入这个问题。我们可以看到，当gender特征变成gender_male和gender_female的时候，这两个特征之间其实是冗余的。因为我们如果gender_male=1，那么gender_female=0，反过来也一样。这个问题在数学中称为多重共线性 （Multicollinearity），在pandas处理的时候有人也叫它虚拟变量陷阱（Dummy Variable Trap）。

这个问题的一个解决方法是加入参数`drop_first=True`。这也是`pandas.get_dummies`的一个参数，它的作用是去除第一个虚拟变量，让转换后的虚拟变量个数从原来的k个变成k-1个。例如，前面的gender变成gender_male和gender_female，如果设置`drop_first=True`，那么会导致结果去除了gender_male，只剩下gender_female，这样剩下的变量就没有这个问题了。如下图所示：

![img](assets/891b6526-49e5-4df0-97dc-8424eee6abce.png)



对于2个变量以上的情况，依然一样：

![img](assets/f90fcf56-1805-4df5-91cd-5fb1925012d8.png)



因此，在应用`pandas.get_dummies`方法时候，千万要注意`drop_first=True`。



## sample()

DataFrame.sample 是Pandas库中的一个方法，用于从DataFrame对象的指定轴（行或列，默认为行）中随机抽取一些项目，并返回一个新的Series或DataFrame对象，其中包含这些随机抽取的项目。

```python
DataFrame.sample(n=None, frac=None, replace=False, weights=None, random_state=None, axis=None, ignore_index=False)
```



**返回值**

- **Series or DataFrame**



### 参数说明：

**n 指定返回数据的数量，可选参数。**
n:int, optional

- 要从轴中返回的项目数量，可选参数。
- 如果指定了 n ，则不能同时使用 frac。
- 默认情况下，如果未提供frac，则n默认为1，即默认返回一个项目。



**frac 按比例返回数据，可选参数。**
frac:float, optional

- 要从轴中返回的项目的比例，可选参数。
- 如果指定了 frac ，则不能同时使用 n 。
- frac 的值应该是一个小数，表示要返回的项目占轴上项目总数的比例。

**replace 是否允许对同一行（或列）进行多次抽样**
replace:bool, default False

- 一个布尔值，表示是否允许对同一行（或列）进行多次抽样。
- 如果设置为True，则可能多次抽样相同的行（或列）
- 如果设置为False，则不允许多次抽样相同的行（或列）。
  注意：当frac > 1 ，replace必须为True。

**weights 用于指定抽样时的权重**
weights:str or ndarray-like, optional

- 一个字符串或类似ndarray的对象，可选参数。用于指定抽样时的权重。默认情况下，所有项目具有相等的抽样概率。你可以通过提供权重来改变抽样概率，例如，你可以为不同的行或列分配不同的权重。

- 使用DataFrame列作为权重，列中值较大的行更有可能被采样。



**random_state 随机数生成器的种子**
random_state:int, array-like, BitGenerator, np.random.RandomState, np.random.Generator, optional

- *一个整数*、*array-like*、*BitGenerator*、*np.random.RandomState*、*np.random.Generator*或*None*，可选参数。用于控制随机数生成器的种子，以确保结果的可重现性。



**axis 指定要从哪个轴上进行抽样**
axis:{0 or ‘index’, 1 or ‘columns’, None}, default None

- 指定要从哪个轴上进行抽样，可以是0（表示行）或1（表示列），也可以使用’index’或’columns’来表示。默认为None，表示根据数据类型的统计轴进行抽样。
  

**ignore_inde** 是否在返回的结果中忽略索引

ignore_index:\*bool, default False

- 一个布尔值，表示是否在返回的结果中忽略索引。
  如果设置为True，返回的结果将不再使用原来的索引。会重新应用一个具有默认的0到n-1的整数索引。



### 例子：

1、首先定义一个数据，结构如下：

```python
import pandas as pd

# 定义一组数据
df = pd.DataFrame({'num_legs': [2, 4, 8, 0],
                   'num_wings': [2, 0, 0, 0],
                   'num_specimen_seen': [10, 2, 1, 8]},
                  index=['falcon', 'dog', 'spider', 'fish'])
print(df)
"""
        num_legs  num_wings  num_specimen_seen
falcon         2          2                 10
dog            4          0                  2
spider         8          0                  1
fish           0          0                  8
"""

```



2、从`Series` `df['num_legs']中随机提取3个元素。注意我们使用`random_state（类似于random库中随机种子的作用）确保示例的可复现性。可以看出，结果是在上述数据的“num_legs”项中随机抽取三个。

```python
extract = df['num_legs'].sample(n=3, random_state=1)
print(extract)
"""
fish      0
spider    8
falcon    2
Name: num_legs, dtype: int64
"""
```



3、replace=True时表示有放回抽样，设置frac=0.5表示随机抽取50%的数据，默认对行数据进行操作。栗子如下。

```python

extract2 = df.sample(frac=0.5, replace=True, random_state=1)
print(extract2)
"""
Name: num_legs, dtype: int64
      num_legs  num_wings  num_specimen_seen
dog          4          0                  2
fish         0          0                  8
"""
```



4、一个上采样的栗子。设置 frac=2。注意，当frac>1时必须设置replace=True，默认对行数据进行操作。

```python

extract3 = df.sample(frac=2, replace=True, random_state=1)
print(extract3)
"""
        num_legs  num_wings  num_specimen_seen
dog            4          0                  2
fish           0          0                  8
falcon         2          2                 10
falcon         2          2                 10
fish           0          0                  8
dog            4          0                  2
fish           0          0                  8
dog            4          0                  2

"""
```



5、使用数据中的某列的数据值作为权重的栗子。对num_availen_seen列数据进行操作，该列数据中值较大的行更容易被采样。可以看出，num_availen_seen列中的数据为[10, 2, 1, 8]，则[10, 8]两列更易被抽到。抽样结果即说明了这一点。

```python

extract4 = df.sample(n=2, weights='num_specimen_seen', random_state=1)
print(extract4)
"""
        num_legs  num_wings  num_specimen_seen
falcon         2          2                 10
fish           0          0                  8
"""
```



#### 汇总

```python
import pandas as pd

# 定义一组数据
df = pd.DataFrame({'num_legs': [2, 4, 8, 0],
                   'num_wings': [2, 0, 0, 0],
                   'num_specimen_seen': [10, 2, 1, 8]},
                  index=['falcon', 'dog', 'spider', 'fish'])
print(df)

extract = df['num_legs'].sample(n=3, random_state=1)
print(extract)

extract2 = df.sample(frac=0.5, replace=True, random_state=1)
print(extract2)

extract3 = df.sample(frac=2, replace=True, random_state=1)
print(extract3)

extract4 = df.sample(n=2, weights='num_specimen_seen', random_state=1)
print(extract4)
```



## interpolate()

pandas 的插值填充函数

```
interpolate ( method = '线性' , * , axis = 0 , limit = None , inplace = False , limit_direction = None , limit_area = None , downcast = _NoDefault.no_default , ** kwargs ) 
```

使用插值方法填充 NaN 值。

请注意，仅`method='linear'`支持具有多重索引的 DataFrame/Series。

### 参数

- **method ：str，默认“linear” **
  - 要使用的插值技术。之一：
  - 'linear'：忽略索引并将值视为等距。这是多索引支持的唯一方法。
  - “time”：适用于每日数据和更高分辨率的数据，以插入给定的间隔长度。
  - 'index', 'values'：使用索引的实际数值。
  - 'pad'：使用现有值填充 NaN。
  - 'nearest'、'zero'、'slinear'、'quadratic'、'cubic'、'barycentric'、'polynomial'：传递给 `scipy.interpolate.interp1d`，而 'spline' 传递给 `scipy.interpolate.UnivariateSpline`。这些方法使用指数的数值。 “polynomial(多项式)”和“spline(样条曲线)”都要求您还指定一个order（int），例如 。请注意， Pandas 中的 slinear(线性方法)是指 Scipy 一阶样条 而不是 Pandas 一阶样条。`df.interpolate(method='polynomial',order=5)`
  - 'krogh'、'piecewise_polynomial'、'spline'、'pchip'、'akima'、'cubicspline'：类似名称的 SciPy 插值方法的包装。参见注释。
  - 'from_derivatives'：指 scipy.interpolate.BPoly.from_derivatives。
- **axis： {{0 或 'index', 1 或 'columns', None}}, 默认 None**
  - 沿其插补的轴。对于系列，此参数未使用，默认为 0。
- **limit：int，可选**
  - 要填充的连续 NaN 的最大数量。必须大于 0.
- **limit_direction：{{'forward'， 'backward'， 'both'}}， 可选**
  - 连续的 NaN 将按此方向填充。
  - 如果指定了 limit：
    - 如果 'method' 是 'pad' 或 'ffill'，则 'limit_direction' 必须是 'forward'。
    - 如果 'method' 是 'backfill' 或 'bfill'，则 'limit_direction' 必须是 'backwards' 的。
  - 如果未指定 'limit'：
    - 如果 'method' 是 'backfill' 或 'bfill'，则默认是 'backward'否则为 'forward'
  - 如果limit_direction是 'forward' 或 'both' 和
    - method 是 'backfill' 或 'bfill'。
  - 如果limit_direction是 'backward' 或 'both' 和
    - method 是 'pad' 或 'ffill'。
- **limit_area :{{ None , 'inside', 'outside'}}, 默认 None**
  - 如果指定了 limit，则连续的 NaN 将用此限制填充。`None`：无填充限制。'inside'：仅填充由有效值包围的 NaN（插值）。'outside'：仅填充有效值之外的 NaN（推断）。
- **downcast: 可选，'infer' 或 None，默认为 None**
  - 如果可能的话，向下转换数据类型。

### 返回：

- **Series 或 DataFrame 或 None**
  - 返回与调用者相同的对象类型，在某些或所有`NaN`值处进行插值，如果是则为 None `inplace=True`。

### 案例

在Pandas中，我们可以使用`interpolate()`方法来进行线性插值。这个方法在Series和DataFrame上都可以使用。

```python
import numpy as np
import pandas as pd

# 创建DataFrame，包含缺失值
df = pd.DataFrame({'A': [1, 2, np.nan, 4, 5], 'B': [np.nan, 7, 8, 9, 10]})

# 线性插值

print(df.interpolate())
"""
     A     B
0  1.0   NaN
1  2.0   7.0
2  3.0   8.0
3  4.0   9.0
4  5.0  10.0
"""
```

由于df的第一个元素中，A列的第二个元素是NaN，所以第一行输出了值为NaN。 然而，通过使用线性插值，我们可以对缺失值进行逐步填补，输出了一个完整的数据框。

#### 选择插值类型

Pandas的`interpolate()`函数默认使用线性插值，但是我们也可以通过`method`参数来选择其他不同的插值方法。例如，如果我们想使用二次插值，可以将`method`参数设置为`quadratic`。

```python
import numpy as np
import pandas as pd

# 创建DataFrame，包含缺失值
df = pd.DataFrame({'A': [1, 2, np.nan, 4, 5], 'B': [np.nan, 7, 8, 9, 10]})

# 线性插值

print(df.interpolate(method='quadratic')) # 使用二次插值
"""
     A     B
0  1.0   NaN
1  2.0   7.0
2  3.0   8.0
3  4.0   9.0
4  5.0  10.0
"""
```

我们可以使用`method`参数，多次使用不同的插值方法，来获取更好的结果。

#### 自定义插值方式

有时候，我们需要自定义插值方式，以此来获取更好的结果。在Pandas中，我们可以通过传递一个函数来进行自定义插值方式。

例如，假设我们希望使用列的平均值来填充缺失值。我们可以定义一个函数来实现这个功能，并将其传递给`interpolate()`函数：

```python
import numpy as np
import pandas as pd

# 创建DataFrame，包含缺失值
df = pd.DataFrame({'A': [1, 2, np.nan, 4, 5], 'B': [np.nan, 7, 8, 9, 10]})

# 自定义平均值填充缺失值
def fill_nan(col):
    # 计算列的平均值用于填充缺失值
    mean_value = col.mean()
    # 使用平均值填充缺失值
    return col.fillna(mean_value)  # 用这个平均值替换该列中的所有 NaN 值

# 使用自定义插值方式
print(df.apply(fill_nan))
# apply() 是 Pandas 中非常强大的工具，它会自动将函数作用于每一列（默认情况下 axis=0，按列操作）。
"""
     A     B
0  1.0   8.5
1  2.0   7.0
2  3.0   8.0
3  4.0   9.0
4  5.0  10.0
"""
```

如上例所示，用自定义函数处理NaN的情况非常有用，并且能够让我们更好地控制缺失值的填充方式。

#### 处理边缘情况

在使用线性插值时，我们需要注意处理边缘情况。如果数据的第一个或最后一个元素是NaN，则线性插值无法顺利开始或结束。

Pandas的`interpolate()`方法提供了两种处理边缘情况的方法，分别是`pad`和`backfill`。`pad`将使用第一个非NaN值填充开头缺失的值，而`backfill`将使用最后一个非NaN值填充结尾缺失的值。让我们看下面的示例，说明如何使用这两种方法：

```python
import numpy as np
import pandas as pd
# 创建DataFrame，包含在边缘处出现的缺失值
df2 = pd.DataFrame({'A': [np.nan, 2, 3, 4, np.nan], 'B': [6, 7, 8, 9, np.nan]})

# 在开始处使用前向填充
df3 = df2.interpolate(method='linear', limit_direction='forward')
print(df3)
"""
     A    B
0  NaN  6.0
1  2.0  7.0
2  3.0  8.0
3  4.0  9.0
4  4.0  9.0
"""

# 在结尾处使用反向填充
df4 = df2.interpolate(method='linear', limit_direction='backward')
print(df4)
"""
     A    B
0  2.0  6.0
1  2.0  7.0
2  3.0  8.0
3  4.0  9.0
4  NaN  NaN
"""
```

在上述代码中，我们向DataFrame添加了两个NaN值。在这种情况下，我们使用`interpolate()`方法，并在`limit_direction`参数中指定填充方法，以便在开始或结束时启动插值。

请注意，第一个示例使用前向填充方法，它将第一个NaN值用2来填补；而第二个示例使用了反向填充方法，将最后一个NaN值用9来填补。



#### 处理时间序列数据

在处理时间序列数据时，当我们需要进行缺失值的插值时，线性插值同样可行，我们只需要简单地将时间序列列标记为索引列即可。在Pandas中，我们可以使用`interpolate()`方法进行这种插值。

例如，让我们使用2019年1月1日至1月31日之间每日最高温度的数据进行插值。请注意，在这个示例中，我们需要使用`DataFrame.reindex()`函数对日期范围进行重命名，以确保我们有一个完整的时间序列：

```python
import numpy as np
import pandas as pd
# 创建示例数据
dates = pd.date_range(start='1/1/2019', end='1/30/2019', freq='D')
df3 = pd.DataFrame({'temperature': [np.nan, 15, 16, np.nan, 17, 16, np.nan, 18, np.nan, 20, 19, 15, 15, np.nan, 16,
                                    17, 16, np.nan, 19, 20, 17, 16, 16, 15, np.nan, 17, 15, np.nan, 16, 16]})
print(dates)
"""
DatetimeIndex(['2019-01-01', '2019-01-02', '2019-01-03', '2019-01-04',
               '2019-01-05', '2019-01-06', '2019-01-07', '2019-01-08',
               '2019-01-09', '2019-01-10', '2019-01-11', '2019-01-12',
               '2019-01-13', '2019-01-14', '2019-01-15', '2019-01-16',
               '2019-01-17', '2019-01-18', '2019-01-19', '2019-01-20',
               '2019-01-21', '2019-01-22', '2019-01-23', '2019-01-24',
               '2019-01-25', '2019-01-26', '2019-01-27', '2019-01-28',
               '2019-01-29', '2019-01-30'],
              dtype='datetime64[ns]', freq='D')
"""
# 设置日期为索引
df3.index = dates

print(df3)
"""
            temperature
2019-01-01          NaN
2019-01-02         15.0
2019-01-03         16.0
2019-01-04          NaN
2019-01-05         17.0
2019-01-06         16.0
2019-01-07          NaN
2019-01-08         18.0
2019-01-09          NaN
2019-01-10         20.0
2019-01-11         19.0
2019-01-12         15.0
2019-01-13         15.0
2019-01-14          NaN
2019-01-15         16.0
2019-01-16         17.0
2019-01-17         16.0
2019-01-18          NaN
2019-01-19         19.0
2019-01-20         20.0
2019-01-21         17.0
2019-01-22         16.0
2019-01-23         16.0
2019-01-24         15.0
2019-01-25          NaN
2019-01-26         17.0
2019-01-27         15.0
2019-01-28          NaN
2019-01-29         16.0
2019-01-30         16.0
"""
# 进行跨列插值
print(df3.interpolate(method='time'))
"""
            temperature
2019-01-01          NaN
2019-01-02         15.0
2019-01-03         16.0
2019-01-04         16.5
2019-01-05         17.0
2019-01-06         16.0
2019-01-07         17.0
2019-01-08         18.0
2019-01-09         19.0
2019-01-10         20.0
2019-01-11         19.0
2019-01-12         15.0
2019-01-13         15.0
2019-01-14         15.5
2019-01-15         16.0
2019-01-16         17.0
2019-01-17         16.0
2019-01-18         17.5
2019-01-19         19.0
2019-01-20         20.0
2019-01-21         17.0
2019-01-22         16.0
2019-01-23         16.0
2019-01-24         15.0
2019-01-25         16.0
2019-01-26         17.0
2019-01-27         15.0
2019-01-28         15.5
2019-01-29         16.0
2019-01-30         16.0
"""
```

在本例中，我们将时间序列数据赋给日期，并将其设置为索引列。我们还在数据中添加了一些NaN值。接下来，我们执行`interpolate()`方法，并得到了一个完整的数据集。
