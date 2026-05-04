# 常见的数据类型

![image-20240916212620679](assets/image-20240916212620679.png)



# 数组



## 创建数组

```python
import numpy as np

t1 = np.array([1, 2, 3])
t2 = np.array(range(1, 4))
t3 = np.arange(1, 4)
t4 = np.array([1,0,1,0,1])
print(t1, t1.dtype)
print(t2, t2.dtype)
print(t3, t3.dtype)
"""
[1 2 3] int64
[1 2 3] int64
[1 2 3] int64
[1 0 1 0 1] int64
"""
```



### 定义数组数据类型

```python
import numpy as np

t1 = np.array([1, 2, 3],dtype="int8")
t2 = np.array(range(1, 4),dtype="float16")
t3 = np.arange(1, 4, dtype="uint8")
t4 = np.array([1,0,1,0,1],dtype=bool)
print(t1, t1.dtype)
print(t2, t2.dtype)
print(t3, t3.dtype)
print(t4, t4.dtype)

"""
[1 2 3] int8
[1. 2. 3.] float16
[1 2 3] uint8
[ True False  True False  True] bool
"""
```



### 修改定义好的数组类型

```python
import numpy as np

t1 = np.array([-1, 2, 3],dtype="int8")
t2 = np.array(range(1, 4),dtype="float16")
t3 = np.arange(1, 4, dtype="uint8")
t4 = np.array([1,0,1,0,1],dtype=bool)
t5 = t4.astype("int8")
t6 = t1.astype("uint8")
print(t1, t1.dtype)
print(t2, t2.dtype)
print(t3, t3.dtype)
print(t4, t4.dtype)
print("*"*20)
print(t5, t5.dtype)
print(t6, t6.dtype)
"""
[-1  2  3] int8
[1. 2. 3.] float16
[1 2 3] uint8
[ True False  True False  True] bool
********************
[1 0 1 0 1] int8
[255   2   3] uint8
"""
```



### 对数据的精确的选取

```python
import numpy as np
import random

t7 = np.array([random.random() for _ in range(4)])
print(t7,t7.dtype)

t8 = np.round(t7,3)
print(t8,t8.dtype)
print("*"*20)
# 类似
print(round(random.random(),3))
print("%.3f"%random.random())
"""
[0.97861959 0.51744056 0.41695918 0.4766311 ] float64
[0.979 0.517 0.417 0.477] float64
********************
0.905
0.625
"""
```



## 数组形状

通过**数组.shape**可以知道数组是几维数组



修改数组的形状

```python
import numpy as np
import random

t1=np.arange(1,25)
print(t1)

t2 = t1.reshape((4,6))
print(t2)

t3 = t1.reshape((2,3,4))  # 第一个数是分成几大块，后面两个数字看成2维数数组
print(t3)

t4 = t2.reshape((t2.shape[0]*t2.shape[1],))
print(t4)

t5 = t2.flatten()  # 快速展开成一维数组
print(t5)
"""
[ 1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24]
[[ 1  2  3  4  5  6]
 [ 7  8  9 10 11 12]
 [13 14 15 16 17 18]
 [19 20 21 22 23 24]]
[[[ 1  2  3  4]
  [ 5  6  7  8]
  [ 9 10 11 12]]

 [[13 14 15 16]
  [17 18 19 20]
  [21 22 23 24]]]
[ 1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24]
[ 1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24]
"""
```



## 数组计算

数组和数字的计算是对数组各个元素和数字进行计算形成的新数组



如果数组除以0，数组元素只会是nan（0/0）或者inf

- nan是空值，inf是infinity无限大的缩写





---





数组和数组计算（使用数字的运算符），两个数组的形状相同,运算也是相同位置运算进行运算

- ```python
  import numpy as np
  import random
  
  
  
  t2 = np.arange(1,25).reshape((4,6))
  print(t2)
  """
  [[ 1  2  3  4  5  6]
   [ 7  8  9 10 11 12]
   [13 14 15 16 17 18]
   [19 20 21 22 23 24]]
  """
  
  t3 = np.arange(4,28).reshape((4,6))
  print(t3)
  """
  [[ 4  5  6  7  8  9]
   [10 11 12 13 14 15]
   [16 17 18 19 20 21]
   [22 23 24 25 26 27]]
  """
  
  print(t3/t2)
  """
  [[4.         2.5        2.         1.75       1.6        1.5       ]
   [1.42857143 1.375      1.33333333 1.3        1.27272727 1.25      ]
   [1.23076923 1.21428571 1.2        1.1875     1.17647059 1.16666667]
   [1.15789474 1.15       1.14285714 1.13636364 1.13043478 1.125     ]]
  """
  print(t3*t2)
  """
  [[  4  10  18  28  40  54]
   [ 70  88 108 130 154 180]
   [208 238 270 304 340 378]
   [418 460 504 550 598 648]]
  
  """
  
  ```

---



### 广播原则

![image-20240917143357805](assets/image-20240917143357805.png)

- ```python
  import numpy as np
  
  t1 = np.arange(1, 25).reshape((4, 6))
  print(t1,"\n")
  
  t2 = np.arange(1, 7)
  print(t2)
  
  print(t2 + t1)
  
  print("*"*10)
  t3 = np.arange(2, 6).reshape(4, 1)
  print(t3)
  print(t2 - t3)
  
  print("*"*10)
  print(t3-t1)
  """
  [[ 1  2  3  4  5  6]
   [ 7  8  9 10 11 12]
   [13 14 15 16 17 18]
   [19 20 21 22 23 24]] 
  
  [1 2 3 4 5 6]
  [[ 2  4  6  8 10 12]
   [ 8 10 12 14 16 18]
   [14 16 18 20 22 24]
   [20 22 24 26 28 30]]
  **********
  [[2]
   [3]
   [4]
   [5]]
  [[-1  0  1  2  3  4]
   [-2 -1  0  1  2  3]
   [-3 -2 -1  0  1  2]
   [-4 -3 -2 -1  0  1]]
   **********
  [[  1   0  -1  -2  -3  -4]
   [ -4  -5  -6  -7  -8  -9]
   [ -9 -10 -11 -12 -13 -14]
   [-14 -15 -16 -17 -18 -19]]
  """
  ```



## 数组装置

```python
import numpy as np

t1 = np.arange(1, 25).reshape((4, 6))

print(t1)
"""
[[ 1  2  3  4  5  6]
 [ 7  8  9 10 11 12]
 [13 14 15 16 17 18]
 [19 20 21 22 23 24]]
"""

print(t1.transpose())
print(t1.T)
print(t1.swapaxes(1, 0))  # 交换轴
"""
[[ 1  7 13 19]
 [ 2  8 14 20]
 [ 3  9 15 21]
 [ 4 10 16 22]
 [ 5 11 17 23]
 [ 6 12 18 24]]
"""
```





# 轴

![image-20240917150039425](assets/image-20240917150039425.png)



## 二维的轴

![image-20240917150101269](assets/image-20240917150101269.png)

0代表行，1代表列



## 三维的轴

![image-20240917150116309](assets/image-20240917150116309.png)

0代表第几个，1代表行，2代表列



# 数据处理

## 读取本地文件

![image-20240917153036544](assets/image-20240917153036544.png)

```python
import numpy as np

us_file_path = "./data/US_video_data_numbers.csv"
uk_file_path = "./data/GB_video_data_numbers.csv"

t1 = np.loadtxt(us_file_path,delimiter=",",dtype="int")
t2 = np.loadtxt(us_file_path,delimiter=",",dtype="int",unpack=True)  # 矩阵装置

print(t1)
print("*"*100)
print(t2)
""" 
[[4394029  320053    5931   46245]
 [7860119  185853   26679       0]
 [5845909  576597   39774  170708]
 ...
 [ 142463    4231     148     279]
 [2162240   41032    1384    4737]
 [ 515000   34727     195    4722]]
****************************************************************************************************
[[4394029 7860119 5845909 ...  142463 2162240  515000]
 [ 320053  185853  576597 ...    4231   41032   34727]
 [   5931   26679   39774 ...     148    1384     195]
 [  46245       0  170708 ...     279    4737    4722]]

进程已结束，退出代码为 0

"""
```



## 取所需行和列

```python
import numpy as np

us_file_path = "./data/US_video_data_numbers.csv"
uk_file_path = "./data/GB_video_data_numbers.csv"

t1 = np.loadtxt(us_file_path, delimiter=",", dtype="int")
print(t1)
# t2 = np.loadtxt(us_file_path,delimiter=",",dtype="int",unpack=True)  # 矩阵装置

# 取行
print(t1[2])  # [5845909  576597   39774  170708]

# 取连续的多行
print(t1[1:-2])
"""
[5845909  576597   39774  170708]
[[7860119  185853   26679       0]
 [5845909  576597   39774  170708]
 [2642103   24975    4542   12829]
 ...
 [ 483496    1369    1645    1115]
 [   4672     234       0       0]
 [ 142463    4231     148     279]]
"""

# 取不连续的多行
print(t1[[0, 2, 4, -1], :])  # print(t1[[0,2,4,-1]]) 全取可以省略
"""
[[4394029  320053    5931   46245]
 [5845909  576597   39774  170708]
 [1168130   96666     568    6666]
 [ 515000   34727     195    4722]]
"""

print("*" * 100)

# 取列
print(t1[:, 0])
"""
[4394029 7860119 5845909 ...  142463 2162240  515000]
"""
print(t1[:, 0:1])
"""
[[4394029]
 [7860119]
 [5845909]
 ...
 [ 142463]
 [2162240]
 [ 515000]]
"""

# 取连续的多列
print(t1[:,1:3])
"""
[[320053   5931]
 [185853  26679]
 [576597  39774]
 ...
 [  4231    148]
 [ 41032   1384]
 [ 34727    195]]
"""

# 取不连续的多列
print(t1[:,[1,3]])
"""
[[320053  46245]
 [185853      0]
 [576597 170708]
 ...
 [  4231    279]
 [ 41032   4737]
 [ 34727   4722]]
"""
```



## 取值

```python
import numpy as np

us_file_path = "./data/US_video_data_numbers.csv"
uk_file_path = "./data/GB_video_data_numbers.csv"

t1 = np.loadtxt(us_file_path, delimiter=",", dtype="int")
print(t1)
"""
[[4394029  320053    5931   46245]
 [7860119  185853   26679       0]
 [5845909  576597   39774  170708]
 ...
 [ 142463    4231     148     279]
 [2162240   41032    1384    4737]
 [ 515000   34727     195    4722]]
"""

# 取某一行，某一列的具体值
print(t1[2, 3], type(t1[2, 3]))  # 取第3行第4列的具体值
"""
170708 <class 'numpy.int64'>
"""

# 取多行多列
print(t1[2:5, 1:4])  # 取第3行到第5行，第2列到第4列的数据
"""
[[576597  39774 170708]
 [ 24975   4542  12829]
 [ 96666    568   6666]]
"""

# 取多个不相邻的点
print(t1[[2, 3, 2], [1, 3, 1]])
"""
[576597  12829 576597]
"""
```



### 取最各行（列）最大值或最小值

```python
import numpy as np

t1 = np.arange(1, 25).reshape((4, 6)).T
"""
[[ 1  7 13 19]
 [ 2  8 14 20]
 [ 3  9 15 21]
 [ 4 10 16 22]
 [ 5 11 17 23]
 [ 6 12 18 24]]
"""

c=np.argmax(t1,axis=1)  # 找出最大元素位置
print(c)  # [3 3 3 3 3 3]

f=np.argmin(t1,axis=0)  # 找出最小元素位置
print(f)  # [0 0 0 0]
```





## 修改值

如果是（多）行，多（列）进行修改值可以直接用上面的方法如何加**=xx**即可，如是符合条件的进行修改

```python
import numpy as np
t = np.arange(1, 25).reshape((4, 6)).T
print(t)
"""
[[ 1  7 13 19]
 [ 2  8 14 20]
 [ 3  9 15 21]
 [ 4 10 16 22]
 [ 5 11 17 23]
 [ 6 12 18 24]]
"""

print(t < 10)  # 转换成bool类型，
"""
[[ True  True False False]
 [ True  True False False]
 [ True  True False False]
 [ True False False False]
 [ True False False False]
 [ True False False False]]
"""
print(t[t < 10])  # 输出小于10的数据
"""
[1 7 2 8 3 9 4 5 6]
"""

t[t < 10] = 3
print(t)
"""
[[ 3  3 13 19]
 [ 3  3 14 20]
 [ 3  3 15 21]
 [ 3 10 16 22]
 [ 3 11 17 23]
 [ 3 12 18 24]]
"""
```



### 三元运算符

如果像要小于10的部分为0，大于等于10的部分为0

```python
import numpy as np

t = np.arange(1, 25).reshape((4, 6)).T
print(t)
"""
[[ 1  7 13 19]
 [ 2  8 14 20]
 [ 3  9 15 21]
 [ 4 10 16 22]
 [ 5 11 17 23]
 [ 6 12 18 24]]
"""
# 如果像要小于10的部分为0，大于等于10的部分为0
t1 = np.where(t < 10, 0, 10)
print(t1)
"""
[[ 0  0 10 10]
 [ 0  0 10 10]
 [ 0  0 10 10]
 [ 0 10 10 10]
 [ 0 10 10 10]
 [ 0 10 10 10]]
"""
t[t < 10] = 0
t[t >= 10] = 10
print(t)
"""
[[ 0  0 10 10]
 [ 0  0 10 10]
 [ 0  0 10 10]
 [ 0 10 10 10]
 [ 0 10 10 10]
 [ 0 10 10 10]]
"""
```



### clip裁剪

![image-20240918215736916](assets/image-20240918215736916.png)

## 计算各行（各列）有关的算法



如果数组中含有nan元素，nan和任何值计算都为nan

```python
import numpy as np

t = np.arange(1, 25, dtype="float32").reshape((4, 6)).T
t = np.where(t < 10., 0., 10.)
print(t)
"""
[[ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0. 10. 10. 10.]
 [ 0. 10. 10. 10.]
 [ 0. 10. 10. 10.]]
"""
# 计算所有数组元素之和
print(np.sum(t))  # 150.0

# 计算数组各列的元素之和
print(np.sum(t, axis=0))  # [ 0. 30. 60. 60.] 轴是0看做沿着行方向计算
# 计算数组各行的元素之和
print(np.sum(t, axis=1))  # [20. 20. 20. 30. 30. 30.] 轴是1看做沿着列方向计算
```



### 关于nan的处理



![image-20240918220157374](assets/image-20240918220157374.png)



计算nan的个数

```python
import numpy as np

t = np.arange(1, 25,dtype="float32").reshape((4, 6)).T
t = np.where(t < 10., 0., 10.)
t[3,:] = np.nan
print(t)
"""
[[ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [nan nan nan nan]
 [ 0. 10. 10. 10.]
 [ 0. 10. 10. 10.]]
"""
# 判断数组元素中是否有nan元素，如果有则为True
print(t!=t)
print(np.isnan(t))

"""
结果都是这样
[[False False False False]
 [False False False False]
 [False False False False]
 [ True  True  True  True]
 [False False False False]
 [False False False False]]
"""
```



可以通过np.count_nonzero()函数进行计算，这个函数本身是计算非0的个数

```python
import numpy as np

t = np.arange(1, 25,dtype="float32").reshape((4, 6)).T
t = np.where(t < 10., 0., 10.)
t[3,:] = np.nan
print(t)
"""
[[ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [nan nan nan nan]
 [ 0. 10. 10. 10.]
 [ 0. 10. 10. 10.]]
"""

# 计算非0个数
print(np.count_nonzero(t))  # 16

# nan 是不等于nan的

# 计算nan的个数
print(np.count_nonzero(t!=t)) # 4
print(np.count_nonzero(np.isnan(t)))  # 4
```



修改数组中nan元素为其他值

```python
import numpy as np

t = np.arange(1, 25,dtype="float32").reshape((4, 6)).T
t = np.where(t < 10., 0., 10.)
t[3,:] = np.nan
print(t)
"""
[[ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [nan nan nan nan]
 [ 0. 10. 10. 10.]
 [ 0. 10. 10. 10.]]
"""

# 修改nan为10
t[np.isnan(t)]=13.5 
# 或者是t[t!=t]=13.5 
print(t)
"""
[[ 0.   0.  10.  10. ]
 [ 0.   0.  10.  10. ]
 [ 0.   0.  10.  10. ]
 [13.5 13.5 13.5 13.5]
 [ 0.  10.  10.  10. ]
 [ 0.  10.  10.  10. ]]
"""
```



注意：nan和任何值计算都为nan



#### 替换nan

一般是替换成均值（中值）或者是直接删除有缺失值的一行或一列（看情况）

```python
import numpy as np

us_file_path = "./data/US_video_data_numbers.csv"
uk_file_path = "./data/GB_video_data_numbers.csv"


def h_nan(t, a=1):
    # 根据数据结构看是安行还是列来进行拟合
    for i in range(t.shape[a]):
        if a == 1:
            temp = t[:, i]  # 按照列来分类
        else:
            temp = t[i, :]  # 按照行分类
        nan_num = np.count_nonzero(np.isnan(temp))
        if nan_num != 0:  # 不为0，说明当前这一列中有nan
            temp_not_nan = temp[temp == temp]  # 取出当前对应的数组不为nan的部分
            temp[np.isnan(temp)] = temp_not_nan.mean()
    return t


if __name__ == '__main__':
    t = np.arange(1, 25, dtype="float32").reshape((4, 6)).T
    t = np.where(t < 10., 0., 10.)
    t[3, :] = np.nan
    print(t)
    """
    [[ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [nan nan nan nan]
 [ 0. 10. 10. 10.]
 [ 0. 10. 10. 10.]]
    """
    t1 = h_nan(t, 1)
    print(t1)
    """
    [[ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0.  0. 10. 10.]
 [ 0.  4. 10. 10.]
 [ 0. 10. 10. 10.]
 [ 0. 10. 10. 10.]]
    """
```





### 其他算法

写法和上面的sum类似就写了

注意下面写的是np.xxx那就只能写成那样，其他的可以写成np.xxx或者数组.xxx即可

![image-20240919173109709](assets/image-20240919173109709.png)



## 数组拼接

```python
import numpy as np

t1 = np.arange(1, 25).reshape((4, 6)).T
"""
[[ 1  7 13 19]
 [ 2  8 14 20]
 [ 3  9 15 21]
 [ 4 10 16 22]
 [ 5 11 17 23]
 [ 6 12 18 24]]
"""
t2 = np.arange(63,87).reshape((4, 6)).T
"""
[[63 69 75 81]
 [64 70 76 82]
 [65 71 77 83]
 [66 72 78 84]
 [67 73 79 85]
 [68 74 80 86]]
"""
t=np.vstack((t1, t2))  # 竖直拼接
print(t)
"""
[[ 1  7 13 19]
 [ 2  8 14 20]
 [ 3  9 15 21]
 [ 4 10 16 22]
 [ 5 11 17 23]
 [ 6 12 18 24]
 [63 69 75 81]
 [64 70 76 82]
 [65 71 77 83]
 [66 72 78 84]
 [67 73 79 85]
 [68 74 80 86]]
"""
t=np.hstack((t1, t2))  # 水平拼接
print(t)
"""
[[ 1  7 13 19 63 69 75 81]
 [ 2  8 14 20 64 70 76 82]
 [ 3  9 15 21 65 71 77 83]
 [ 4 10 16 22 66 72 78 84]
 [ 5 11 17 23 67 73 79 85]
 [ 6 12 18 24 68 74 80 86]]
"""
```



## 数组的行（列）交换

![image-20240921155953820](assets/image-20240921155953820.png)



## 生成随机数组

![image-20240921163512901](assets/image-20240921163512901.png)



## 拷贝



### 浅拷贝

a=b 这种是完全不复制，a和b相互影响

```python
import numpy as np

t1 = np.arange(1, 13).reshape((3, 4)).T
print(t1)
"""
[[ 1  5  9]
 [ 2  6 10]
 [ 3  7 11]
 [ 4  8 12]]
"""
a = t1
a[a<5]=33
print(a)
print(t1)
"""
[[33  5  9]
 [33  6 10]
 [33  7 11]
 [33  8 12]]
[[33  5  9]
 [33  6 10]
 [33  7 11]
 [33  8 12]]
"""
```

a=b[:]，视图的操作，一种切片，会创建新的对象a，但是a的数据完全由b保管，他们两个数据变化是一致的

```python
import numpy as np

t1 = np.arange(1, 13).reshape((3, 4)).T
print(t1)
"""
[[ 1  5  9]
 [ 2  6 10]
 [ 3  7 11]
 [ 4  8 12]]
"""
a = t1[:]
a[a<5]=33
print(a)
print(t1)
"""
[[33  5  9]
 [33  6 10]
 [33  7 11]
 [33  8 12]]
[[33  5  9]
 [33  6 10]
 [33  7 11]
 [33  8 12]]
"""
```



### 深拷贝

a=b.copy(),复制，a和b互不影响

```python
import numpy as np

t1 = np.arange(1, 13).reshape((3, 4)).T
print(t1)
"""
[[ 1  5  9]
 [ 2  6 10]
 [ 3  7 11]
 [ 4  8 12]]
"""
a = t1.copy()
a[a<5]=33
print(a)
print(t1)
"""
[[33  5  9]
 [33  6 10]
 [33  7 11]
 [33  8 12]]
[[ 1  5  9]
 [ 2  6 10]
 [ 3  7 11]
 [ 4  8 12]]
"""
```



# 常用函数

## numpy.squeeze(a,axis = None)

```
 1）a表示输入的数组；
 2）axis用于指定需要删除的维度，但是指定的维度必须为单维度，否则将会报错；
 3）axis的取值可为None 或 int 或 tuple of ints, 可选。若axis为空，则删除所有单维度的条目；
 4）返回值：数组
 5) 不会修改原数组；
```

- **作用**：
  - 从数组的形状中删除单维度条目，即把shape中为1的维度去掉
- **场景**：
  - 在机器学习和深度学习中，通常算法的结果是可以表示向量的数组（即包含两对或以上的方括号形式[[]]），如果直接利用这个数组进行画图可能显示界面为空（见后面的示例）。我们可以利用squeeze（）函数将表示向量的数组转换为秩为1的数组，这样利用matplotlib库函数画图时，就可以正常的显示结果了。

## maximun

`np.maximum` 是 NumPy 库中的一个函数，用于计算两个数组或数组与标量之间的元素级最大值。该函数会逐个比较两个输入中的元素，并返回对应位置上的较大值。如果其中一个输入是标量，则将其广播到另一个输入的形状。

**参数：**

- x1, x2：数组或标量。这两个参数是要进行元素级比较的对象。
- out（可选）：存储结果的数组。
- where（可选）：布尔数组，指定在何处计算函数。
- dtype（可选）：输出数组的数据类型。



**返回值：**

- 返回一个新的数组，包含 x1 和 x2 中对应位置的最大值。



**示例：**

```python
import numpy as np

# 比较两个数组
a = np.array([1, 2, 3])
b = np.array([4, 1, 2])
result = np.maximum(a, b)
print(result)  # 输出: [4 2 3]


# 比较数组和标量
c = 5
result = np.maximum(a, c)
print(result)  # 输出: [5 5 5]
print("#"*10)
np.maximum(a,b,out=a)
print(a)  # 输出: [4 2 3]
print("#"*10)

for i in range(3):
    print(np.maximum(a,c,where=i==1))
"""
[4 2 3]
[5 5 5]
[5 5 5]
"""
```



## random.choice

`np.random.choice `是 NumPy 库中的一个函数，用于从给定的一维数组中随机抽取元素。该函数可以用于有放回或无放回的抽样，并且可以指定每个元素被抽中的概率。

```python
numpy.random.choice(a, size=None, replace=True, p=None)

```

**参数说明：**

- a：一维数组或整数。如果为整数，则表示从 np.arange(a) 中抽取；如果为数组，则直接从数组中抽取。
- size：输出的形状，默认为 None，即返回单个元素。可以指定为整数或元组，例如 (m, n) 表示返回 m x n 的数组。
- replace：布尔值，默认为 True，表示是否允许重复抽样。如果为 False，则表示无放回抽样。
- p：与 a 相同长度的概率数组，表示每个元素被抽中的概率。如果不指定，则默认所有元素被抽中的概率相同。

**返回值：**

- 根据 size 参数返回一个随机抽样的数组或单个元素。

**示例用法**

```python
import numpy as np

# 从 [0, 1, 2, 3] 中随机抽取 5 个元素，允许重复
result = np.random.choice(4, size=5, replace=True)
print(result)

# 从 ['apple', 'banana', 'orange'] 中随机抽取 3 个元素，不允许重复
result = np.random.choice(['apple', 'banana', 'orange'], size=3, replace=False)
print(result)

# 从 [0, 1, 2, 3] 中随机抽取 5 个元素，指定概率
result = np.random.choice(4, size=5, replace=True, p=[0.1, 0.3, 0.4, 0.2])
print(result)

```



## np.where()

### np.where(condition, x, y)

condition：条件
x：满足条件时函数的输出
y：不满足条件时的输出

```python
>>> import numpy as np
>>> x = np.arange(6).reshape(2, 3)
>>> x
array([[0, 1, 2],
       [3, 4, 5]])
>>> np.where(x > 1, True, False)
array([[False, False,  True],
       [ True,  True,  True]])

```



### np.where(condition)

没有x和y参数，则以元组形式输出满足条件的列表索引。

```python
>>> np.where(x > 1)
(array([0, 1, 1, 1], dtype=int64), array([2, 0, 1, 2], dtype=int64))

```

这个pytorch中的torch.where是一样的



## np.argwhere()

`np.argwhere(condition) `返回非0的数组元组的索引，其中condition是要索引数组的条件。

```python
>>> np.argwhere(x > 1)
array([[0, 2],
       [1, 0],
       [1, 1],
       [1, 2]], dtype=int64)

```





## np.nonzero()

numpy中nonzero()函数可以提取出矩阵中非零元素的行列。用法如下：

```python
import numpy as np

data = np.array([[1, 0, 2], [0, 3, 0], [0, 0, 0]])
print(data)
print(data.nonzero())
"""
[[1 0 2]
 [0 3 0]
 [0 0 0]]
(array([0, 0, 1], dtype=int64), array([0, 2, 1], dtype=int64))
"""
```

可以看到，非零元素被分成行和列。

```python
import numpy as np
a = np.array([[1,2,3],[4,2,2]])
b = a==2
print(np.nonzero(b))
print(b.nonzero())
print(np.argwhere(b))
"""
(array([0, 1, 1], dtype=int64), array([1, 1, 2], dtype=int64))
(array([0, 1, 1], dtype=int64), array([1, 1, 2], dtype=int64))
[[0 1]
 [1 1]
 [1 2]]
"""
```



