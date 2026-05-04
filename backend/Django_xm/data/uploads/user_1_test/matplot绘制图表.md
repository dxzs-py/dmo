

# 一、Matplotlib基本介绍

- Matplotlib（mathematic plot lib，数学绘图库）是一个可**视化绘图库**。可以使用这个库轻松地完成线形图、直方图、条形图、误差图或散点图等操作，设置标签、图例、调整绘图大小等。是python中使用最广的绘图库。有很多库（如pandas，seaborn）的绘图功能都是基于matplotlib实现的。

- pyplot（python plot，python绘图）是Matplotlib的一个基于状态的接口，可以快速的绘制图表，通常我们绘图只需要通过pyplot的接口就可以了。



# 二、两种绘图方式区别（plt.*** 和ax.***）

![img](D:/learning_libray/typora_zsl/assets/cf8e33de675f7f2b05bb84f596cbb2a4.png)



利用matplotlib画图有两种方式，一个直接调用pyplot模型中的相应函数就可以完成绘图，称为过程式绘图；另一种可以在画布上显示多个图表，称为面向对象绘图，fig.subplots()无参数创建图表，默认创建一个图表。

先matplotlib架构上理解，可以理解为：

matplotlib架构上分为三层  

底层：backend layer

中层：artist layer  

最高层：scripting layer

在任意一层操作都能够实现画图的目的，而且画出来还都一样。但越底层的操作越细节话，越高层越易于人机交互。

**plt. 对应的就是最高层 scripting layer**。这就是为什么它简单上手，但要调细节就不灵了。

**ax.plot 是在 artist layer 上操作。基本上可以实现任何细节的调试。**

---

# 三、如何使用Matplotlib绘图

1. 折线图：

   ```python
   plt.plot(X,Y)
   ```

2. 散点图

   ```python
   plt.scatter(X,Y)
   ```

3. 柱状图

   ```python
   plt.bar(X,Y)
   ```

4. 直方图

   ```python
   plt.hist(array) # array是一维数据
   ```

5. 饼图

   ```python
   plt.pie(data,lables=,autopct='%1.1f%%')
   ```

   说明：
   `1.1f`指的是保留1位小数，后面两个`%`表示已百分号显示

   

6. 箱状图

   ```python
   plt.boxplot(X,Y)
   ```

   补充一下箱线图一些说明
   ![箱线图表示](https://i-blog.csdnimg.cn/blog_migrate/d8f9860ef03a2e41a2af373c1b259a0b.png#pic_center)





---



快速绘图的流程：从左往右![img](D:/learning_libray/typora_zsl/assets/1cad1f5fb357bf1764165ce6562bde0b.png)





## 1、画布—绘画的画板

| 函数接口                       | 函数作用                               | 参数说明                                                     |
| ------------------------------ | -------------------------------------- | ------------------------------------------------------------ |
| plt.figure()                   | 创建一个新的绘图窗口，设置画布大小。   | figsize:参数格式为(width,height)，单位为inch。dpi参数：分辨率，指每inch像素数。默认dpi为100。例figsize=(18,6) |
| fig.add_subplot(row,col,index) | 在新的绘图窗口上创建子图，子图相互独立 | 无显式参数，**逗号可以省略**，fig.add_subplot(2,2,2)与fig.add_subplot(222)都可以。 row:行，col:列，index:子图的位置 |
| plt.show()                     | 显示当前figure图像                     |                                                              |
| plt.close()                    | 关闭当前figure窗口                     |                                                              |



**过程式绘图**

```python
import matplotlib.pyplot as plt
import numpy as np

x = np.random.random(10)
y = np.random.random(10)
print(x)
# [0.96602933 0.89831117 0.71655303 0.20132208 0.45814434 0.35961567 0.68324034 0.67527241 0.36248942 0.07193008]
plt.plot(x, y)  # 绘制拆线图
plt.show()  # 显示图形
```

说明：依次调用pyplot模块中的相应函数就可以完成绘图。

---



**面向对象绘图**

```python
import matplotlib.pyplot as plt
fig=plt.figure(figsize=(18,6))
ax1=fig.add_subplot(2,2,1) # 创建2*2=4张图，ax1画在第一张图上
ax2=fig.add_subplot(2,2,2) # 创建2*2=4张图，ax2画在第二张图上
ax3=fig.add_subplot(2,2,3) # 创建2*2=4张图，ax3画在第三张图上
ax4=fig.add_subplot(2,2,4) # 创建2*2=4张图，ax4画在第四张图上
plt.show()  # 显示画布
plt.close() # 关闭画布
```

说明：

1、先通过plt.figure()函数创建一张完整的画面，作为底层，之后的所有操作都在这张画布上完成。

2、再通过figure.add_subplot()函数创建子图，相当于在已创建的画布上再叠加子图，子画布间相互独立，这样就可以达到一次性完成多幅图片的效果。



3、创建多个子图，可通过循环最后一个参数达到效果。

```python
import matplotlib.pyplot as plt
fig=plt.figure(figsize=(18,6))
 
for i in range(1,5):
	ax=fig.add_subplot(2,2,i)
 
plt.show()
plt.close()
```



4、其他方法创建方法

```python
import matplotlib.pyplot as plt


# 生成3行2列的子图
hs = 3
ls = 2
fig, ax = plt.subplots(hs, ls) 

plt.show()  # 显示画布
plt.close()  # 关闭画布
```



## 2、配置—更个性化的绘图



### 全局配置

过程式和面向对象式配图都适用

**指定中文字体**

| 函数接口                                 | 函数作用     | 参数说明                    |
| :--------------------------------------- | :----------- | :-------------------------- |
| matplotlib.font_manager.FontProperties() | 指定中文字体 | font_path：指定字体的路径。 |

```python
# 指定中文字体样式、大小
import matplotlib.font_manager as mfm
import matplotlib.pyplot as plt

# 请换成自己电脑的字体路径
font_path = r"c:\windows\Fonts\simsun.ttc"
prop = mfm.FontProperties(fname=font_path)
plt.rcParams['axes.unicode_minus']=False #用来正常显示负号

fig = plt.figure()
ax1 = fig.add_subplot(111)
ax1.set_title('你好', fontproperties=prop, fontsize=20)
plt.show()
```

也可以

```python
plt.rcParams['font.family'] = 'SimHei'
plt.rcParams['axes.unicode_minus']=False #用来正常显示负号
```



**指定全局画图主题**

| 函数接口        | 函数作用     | 参数说明                           |
| :-------------- | :----------- | :--------------------------------- |
| plt.style.use() | 指定画图主题 | 无显式参数，style_name：设置主题名 |

```python
# 查看所有主题
print(plt.style.available )
```

效果：

```python
# 指定中文字体样式、大小
import matplotlib.font_manager as mfm
import matplotlib.pyplot as plt

# 请换成自己电脑的字体路径
font_path = r"c:\windows\Fonts\simsun.ttc"
prop = mfm.FontProperties(fname=font_path)

# 指定全局画图主题为Solarize_Light2
plt.style.use('Solarize_Light2')

fig = plt.figure()
ax1 = fig.add_subplot(111)
ax1.set_title('你好', fontproperties=prop, fontsize=20)
plt.show()
```

![image-20240819154834232](D:/learning_libray/typora_zsl/assets/image-20240819154834232.png)



### 局部配置



#### 面向对象绘图过程：ax代表子图变量



##### **图表标题设置**

| 函数接口       | 函数作用                                                     | 参数说明                                                     |
| :------------- | :----------------------------------------------------------- | :----------------------------------------------------------- |
| ax.set_title() | 在当前图形中添加主题，可以指定标题的名称、位置、颜色、字体大小等参数。 | label：标题文字，fontsize：指定字体大小，color：指定字体颜色，loc可选值为{‘left’,‘center’,‘right’}，默认值为’center’ |

> ax.set_title(“sample”)：设置图表标题。
> ax.set_title(“title”, loc=‘left’)：设置图表标题位置。



##### **坐标轴设置**

| 函数接口             | 函数作用                                                     | 参数说明                                                     |
| :------------------- | :----------------------------------------------------------- | :----------------------------------------------------------- |
| ax.set_xlabel()      | 当前图形中添加x轴名称，可以指定标题的名称、位置、颜色、字体大小等参数。 | xlabel：标签文字，fontsize：指定字体大小                     |
| ax.set_ylabel()      | 当前图形中添加y轴名称，可以指定标题的名称、位置、颜色、字体大小等参数。 | ylabel：标签文字，fontsize：指定字体大小                     |
| ax.set_xlim()        | 设置当前图形中x轴的范围，只能确定一个数值区间，而无法使用字符串标识。 | left:指定最小值，right:指定最大值，可以使用tuple合并表示(left,right) |
| ax.set_ylim()        | 设置当前图形中y轴的范围，只能确定一个数值区间，而无法使用字符串标识。 | bottom:指定最小值，top:指定最大值，可以使用tuple合并表示(bottom,top) |
| ax.set_xticks()      | 指定x轴刻度的数目与取值。                                    | ticks：传入列表，每个元素代表一个刻度值。                    |
| ax.set_yticks()      | 指定y轴刻度的数目与取值。                                    | ticks：传入列表，每个元素代表一个刻度值。                    |
| ax.set_xticklabels() | 设置x轴刻度的显示值。                                        | labels：每个刻度上显示的值，fontsize：指定字体大小           |
| ax.set_yticklabels() | 设置y轴刻度的显示值。                                        | labels：每个刻度上显示的值，fontsize：指定字体大小           |
| ax.hlines()          | 设置水平线                                                   | y：某个纵坐标，固定值。xmin：横坐标最小值。xmax：横坐标最大值。 |
| ax.vlines()          | 设置垂直线                                                   | x：某个纵坐标，固定值。ymin：横坐标最小值。ymax：横坐标最大值。 |
| ax2=ax1.twinx()      | 子图ax1、ax2共享x轴                                          | 无                                                           |
| ax2=ax1.twiny()      | 子图ax1、ax2共享y轴                                          | 无                                                           |

> 设置坐标轴标签文本
> ax.set_xlabel(‘X axis’)
> ax.set_ylabel(‘Y axis’)

> 设置x,y轴坐标刻度显示范围
> ax.set_xlim(-3.5,3.5)
> ax.set_ylim(-1.5,1.5)

> 设置主刻度坐标
> ax.set_xticks([-4,-2,0,2,4])
> ax.set_yticks([-1,-0.5,0,0.5,1])
> 设置次刻度坐标
> ax.set_xticks(np.arange(-4,4,0.5),minor=True)
> ax.set_yticks(np.arange(-1.5,1.5,0.1),minor=True)



##### **图例**

| 函数接口  | 函数作用                                           | 参数说明                                                     |
| :-------- | :------------------------------------------------- | :----------------------------------------------------------- |
| ax.legend | 指定当前图形的图例，可以指定图例大小、位置、标签。 | labels：标签名。loc：图例的位置。bbox_to_anchor：指定图例的位置（横坐标，纵坐标）（横纵最大1.0，最小0.0）。prop：等同于fontproperties。fontsize：字体大小。 |

```python
ax.legend(loc='upper right', title='Functions', frameon=True, fontsize=10)
```

- `loc`：指定图例的位置，可以使用字符串或整数值（例如 `loc='upper left'` 或 `loc=1`）。
- `title`：为图例添加标题。
- `frameon`：控制图例框的显示（默认为 True）。
- `fontsize`：设置字体大小。



> ax.legend(loc=‘upper right’):自定义图例位置。

loc 可选择的参数

- ‘best’：自动选择最佳位置
- ‘upper left’：左上角
- ’upper right’：右上角
- ‘lower left’：左下角
- ‘lower right’：右下角
- ‘center left’：中左
- ‘center right’：中右
- ‘lower center’：中下
- ‘upper center’：中上

> ax.legend(fontsize=‘samll’) 自定义图例文字大小。
> ax.legend(fontsize=10)

fontsize 可选择的参数

- 相对大小（字符串）
  ‘xx-small’,‘x-small’,‘smal’：小于当前默认字体大小
  ‘medium’：中等
  ‘large’,‘x-large’,‘xx-large’：大于当前默认字体大小
- 绝对大小（数值）
  fontsize=10：绝对字体大小，单位为点。

> ax.legend(labelcolor=[‘r’,‘b’])：设置图例文本颜色。
> ax.legend(mode=‘expand’)：图例水平平铺。



##### **保存图形**

| 函数接口      | 函数作用 | 参数说明                                 |
| :------------ | :------- | :--------------------------------------- |
| plt.savefig() | 保存图片 | fname：文件名；dpi：像素，单个数值即可。 |

> plt.savefig(“sample.png”)

支持如下格式：

jpg, jpeg：jpg图

png：png图

svg, svgz：svg图

tif, tiff：tiff图

pgf：pgf位图

pdf, eps, ps：pdf或postscript文件

raw, rgba

> 面向对象画图的代码示例



### 颜色和图列

- ***fmt：\***字符串，可选参数。

格式化字符串，例如‘ro’代表红色圆圈。

格式字符串是用于快速设置基本线条样式的缩写，这些样式或更多的样式可通过关键字参数来实现。

```python
fmt = '[color][marker][line]'
```

color（颜色）、marker（标记点）、line（线条）都是可选的，例如如果指定line而不指定marker，将绘制不带标记点的线条。

支持的颜色缩写如下：

| 字符  | 颜色           |
| :---- | :------------- |
| `'b'` | blue 蓝色      |
| `'g'` | green 绿色     |
| `'r'` | red 红色       |
| `'c'` | cyan 青色      |
| `'m'` | magenta 紫红色 |
| `'y'` | yellow 黄色    |
| `'k'` | black 黑色     |
| `'w'` | white 白色     |

支持的marker缩写如下：

| 字符  | 描述                         |
| :---- | :--------------------------- |
| `'.'` | point marker 点              |
| `','` | pixel marker 像素            |
| `'o'` | circle marker 圆形           |
| `'v'` | triangle_down marker 下三角  |
| `'^'` | triangle_up marker 上三角    |
| `'<'` | triangle_left marker 左三角  |
| `'>'` | triangle_right marker 右三角 |
| `'1'` | tri_down marker              |
| `'2'` | tri_up marker                |
| `'3'` | tri_left marker              |
| `'4'` | tri_right marker             |
| `'s'` | square marker 方形           |
| `'p'` | pentagon marker 五角形       |
| `'*'` | star marker 星型             |
| `'h'` | hexagon1 marker 六角形1      |
| `'H'` | hexagon2 marker 六角形2      |
| `'+'` | plus marker 加号             |
| `'x'` | x marker ×型                 |
| `'D'` | diamond marker 钻石型        |
| `'d'` | thin_diamond marker 细钻石型 |
| `'|'` | vline marker 竖线型          |
| `'_'` | hline marker 横线型          |

支持的line缩写如下：

| 字符   | 描述                         |
| :----- | :--------------------------- |
| `'-'`  | solid line style 实线        |
| `'--'` | dashed line style 虚线       |
| `'-.'` | dash-dot line style 交错点线 |
| `':'`  | dotted line style 点线       |



### 案例



#### 面向对象的创建

```python
# 准备数据
# 导入matplotlib库pyplot模块
import matplotlib.pyplot as plt
import numpy as np

x = np.linspace(-np.pi, np.pi, 100)
y = np.sin(x)

# 设置为中文字体
plt.rcParams['font.family'] = 'SimHei'
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

fig, ax = plt.subplots()  # 创建画布与图表


"""
等价于
# 创建画布
fig = plt.figure()

# 创建图表
ax = fig.subplots() 
"""

# 绘制折线图，设置点线样式，设置线条名称
ax.plot(x, y, '+r--', label='迪丽纳尔', mec='b', ms=10)  # 点为蓝色+(大小为10)，线为红色虚线

# 设置坐标轴
ax.set_xlabel('X 轴')  # 坐标轴文本标签
ax.set_ylabel('Y 轴')
ax.set_xticks([-4, -2, 0, 2, 4])  # 主刻度
ax.set_xticks(np.arange(-4, 4, 0.5), minor=True)  # 次刻度
ax.set_yticks([-1, -0.5, 0, 0.5, 1])
ax.set_yticks(np.arange(-1.5, 1.5, 0.1), minor=True)
ax.tick_params(axis='y', labelrotation=30)  # y轴主刻度文字旋转30度
ax.set_xlim(-4.5, 4.5)  # 设置显示刻度范围
ax.set_ylim(-1.5, 1.5)

ax.grid(True)  # 显示主刻度网格

# 设置图例
ax.legend(title="戴兴")  # 注意需要绘图时，需指定label参数

# 设置标题
ax.set_title("样本")

# 保存显示图形
fig.savefig("sample.png")
plt.show()
```

---



#### 过程式的创建

```python
# 导入matplotlib库pyplot模块
import matplotlib.pyplot as plt
import numpy as np

# 设置为中文字体
plt.rcParams['font.family'] = 'SimHei'
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

x = np.linspace(-np.pi, np.pi, 100)
y = np.sin(x)


# 绘制折线图，设置点线样式，设置线条名称
plt.plot(x, y, '+r-', label='迪丽纳尔', mec='b', ms=10)  # 点为蓝色+(大小为10)，线为红色虚线

# 设置坐标轴
plt.xlabel('X 轴')  # 坐标轴文本标签
plt.ylabel('Y 轴')
plt.xticks([-4, -2, 0, 2, 4])  # 主刻度，不支持次刻度设置
plt.yticks([-1, -0.5, 0, 0.5, 1], rotation=30)
plt.xlim(-3.5, 3.5)  # 设置显示刻度范围
plt.ylim(-1.5, 1.5)

plt.grid(True, c='gray', linestyle=':')  # 显示主刻度网格

# 设置图例
plt.legend(title="戴兴")  # 注意需要绘图时，指定label参数

# 设置标题
plt.title("样本")

# 保存显示图形
plt.savefig("sample.png")

plt.show()

```



**效果**

![image-20240819162456370](assets/image-20240819162456370.png)



# 四、常用绘图图形



## 散点图

散点图（scatter diagram，scatter：分散;撒;四散;散开;驱散;撒播）又称为散点分布图，是以一个特征为横坐标，另一个特征为纵坐标，值是由点在图表中的位置表示，类别是由图表中的不同标记表示，通常用于**比较跨类别**的数据。

散点图主要功能利用坐标点（散点）的分布形态反映特征间的统计关系（**相关性**）的一种图形。

1）线性**相关**：x，y变量的散点看上去都在一条直线附近波动；

2）非线性相关：x，y变量的散点都在某条曲线（非直线）附近波动；

3）不相关：所有点在图中没有显示任何关系

### **scatter()函数**

| 函数接口                                                     | 函数作用   |
| :----------------------------------------------------------- | :--------- |
| matplotlib.pyplot.scatter(x, y, s=None, c=None, marker=None, cmap=None, norm=None, vmin=None, vmax=None, alpha=None, linewidths=None, verts=None, edgecolors=None, hold=None, data=None, **kwargs) | 绘制散点图 |

### **基本参数说明：**

| 参数名称   | 说明                                                         |
| :--------- | :----------------------------------------------------------- |
| x，y       | x：自变量。y：因变量。                                       |
| s          | 指定点的大小，数值或者一维的array，若传入一维array则表示每个点的大小。默认为None。 |
| c          | 指定点的颜色，浮点数、颜色或者颜色列表，若传入一维array则表示每个点的颜色，默认为None。 |
| marker     | 表示绘制点的类型，待定string，默认为小圆圈‘o’。              |
| cmap       | Colormap，默认为None，标量或者是一个colormap的名字，只有c是一个浮点数数组的时才使用，如果没有申明就是image.cmap。 |
| norm       | Normalize，默认为None，数据亮度在0-1之间，只有c是一个浮点数的数组时才使用。 |
| vmin,vmax  | 亮度设置，在norm参数存在时忽略。                             |
| alpha      | 表示点的透明度，0-1的小数，默认为None，即不透明。            |
| linewidths | 散点的边缘线宽。                                             |
| edgecolors | 散点的边缘颜色，默认为 ‘face’，可选值有 ‘face’, ‘none’。     |
| **kwargs   | 其他参数。                                                   |



#### 举例1：

```python
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['SimHei']  # 用来显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号
fig, ax = plt.subplots()  # 创建画布与图表

# 准备数据
x1 = np.arange(1, 30)
y1 = np.sin(x1)

ax = plt.subplot(1, 1, 1)
plt.title('散点图')
plt.xlabel('X')
plt.ylabel('Y')
lvalue = x1
# 绘制散点图。颜色为'r'，点的大小为s，散点的边缘线宽为linewidths，点的类型为marker。
ax.scatter(x1, y1, c='r', s=100, linewidths=lvalue, marker='o')
plt.legend('x1')
plt.show()
```

![image-20240819165959040](assets/image-20240819165959040.png)

---



#### 举例2：

```python
import numpy as np
import matplotlib.pyplot as plt
 
fig,ax=plt.subplots()
plt.rcParams['font.family']=['SimHei']  #用来显示中文标签
plt.rcParams['axes.unicode_minus']=False  #用来正常显示负号
 
#绘制散点图。颜色为['red','green','blue']，透明度为0.3，散点的边缘颜色为'none'。
for color in ['red','green','blue']:
	n=500
	x,y=np.random.randn(2,n)
	ax.scatter(x,y,c=color,label=color,alpha=0.3,edgecolors='none')
	ax.legend()
	ax.grid(True)
 
plt.show()
```

![image-20240819171023234](assets/image-20240819171023234.png)

---

#### 举例3：

```python
import numpy as np
import matplotlib.pyplot as plt

fig, ax = plt.subplots()  # 创建画布与图表
# 准备数据
x = np.random.randn(50) * 10  # 随机产生50个x轴坐标
y = np.random.randn(50) * 10  # 随机产生50个y轴坐标
sizes = (np.random.randn(50) * 10) ** 2  # 随机产生50个用于改变散点面积的数值

colors = np.random.randn(50) * 10

# 绘制散点图。颜色为colors，大小为sizes（单位points^2），透明度为0.5
ax.scatter(x, y, s=sizes, c=colors, alpha=0.5)

plt.show()
```

![image-20240819170939471](assets/image-20240819170939471.png)



## 折线图

折线图（line chart）是一种将数据点按照顺序连接起来的图形。可以看作是将散点图，按照x轴坐标顺序连接起来的图形。可以只显示点、只显示线，或者点、线都显示。

折线图的主要功能观察**因变量y随着自变量x改变的趋势**。

- 最适合用于显示**随时间（根据常用比例设置）而变化的连续数据**。
- 适合用于观察**数量的差异，增长趋势**的变化。

### **plot()函数**

| 函数接口                                | 函数作用   |
| :-------------------------------------- | :--------- |
| matplotlib.pyplot.plot(*args, **kwargs) | 绘制折线图 |



### **基本参数说明：**

| 参数名称                      | 说明                                                         |
| :---------------------------- | :----------------------------------------------------------- |
| x，y                          | x：自变量。y：因变量。                                       |
| marker                        | 绘制的点的样式，接受特定string，默认为None，例如marker=‘o’。 |
| markersize                    | 绘制点的大小，ms=1.5。                                       |
| linestyle                     | 线条样式，接受特定string，默认为’-'。                        |
| linewidth                     | 线条宽度                                                     |
| color                         | 线条颜色，接受特定string，默认为None。‘b’：蓝色，‘g’：绿色，’r’：红色，‘c’：青色，’m’：品红，‘y’：黄色，’k’：黑色，’w’：白色。 |
| markerfacecolor               | 点填充颜色。例如mfc=‘r’。                                    |
| markeredgecolor               | 点边缘颜色。例如meg=‘g’。                                    |
| markeredgewidth               | 点边缘宽度。例如mew=0.5。                                    |
| alpha                         | 表示点的透明度，0-1的小数，默认为None。                      |
| fmt = ‘[marker][line][color]’ | fmt为字符串由点样式、颜色、线样式组成，例如’+r–'：点样式为+，颜色为红色，线样式为虚线 |



#### 举例1：

```python
import numpy as np
import matplotlib.pyplot as plt

# 设置为中文字体
plt.rcParams['font.family'] = 'SimHei'
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

fig, ax = plt.subplots()  # 创建画布与图表

# 准备数据
x = np.linspace(0, 4 * np.pi, 50)  # 通过定义均匀间隔创建数值序列
y1 = np.sin(x)
y2 = np.cos(x)
y3 = np.cos(x + np.pi)

# 绘制'line1'。线条为灰色，线形为点划线（'-.'）,线宽为1。
ax.plot(x, y1, c='gray', linestyle='-.', linewidth=1, label='sinx图像')

# 绘制'line2'。线条为红色，线型为虚线（'--'）
ax.plot(x, y2, 'r--', label='cosx图像')

# 绘制'line3'。同时显示点和线。
# 点大小为10，填充颜色为绿色，边缘颜色为红色，边缘宽度为1。
# 线为黑色实绩，线条宽度为2。
ax.plot(x, y3, 'o-', mfc='green', mec='red', ms=10, c="black",linewidth=2, label='cos(Π+x)图像')

ax.legend(loc='upper right',title="三角函数图像")  # 设置图例

ax.grid(True)  # 显示主刻度网格

plt.show()
```

![image-20240819172559587](assets/image-20240819172559587.png)

## 箱线图

箱线图（boxplot）也称箱须图，其绘制需使用数据中的五个常用统计量（**最小值、下四分位数、中位数、上四分位数和最大值**）来描述数据。

箱线图主要功能是能提供有关数据的**位置和分散情况**等相关信息，可以粗略地看出数据是否具有对称性、分布的分散程度等。

适用于比较几个样本，观察其不同特征。

### **boxplot()函数**

| 函数接口                                                     | 函数作用   |
| :----------------------------------------------------------- | :--------- |
| matplotlib.pyplot.boxplot(x, notch=None, sym=None, vert=None, whis=None, positions=None, widths=None, patch_artist=None,meanline=None, labels=None, … ) | 绘制箱线图 |

### **基本参数说明：**

| 参数名称   | 说明                                                         |
| :--------- | :----------------------------------------------------------- |
| x          | 表示用于绘制箱线图的数据，接受array，无默认。                |
| notch      | 表示中间简体是否有缺口，默认为None，接boolean。              |
| sym        | 指定异常点(超出Q0-Q5范围的值的绘制颜色和符号，默认为None，接收特定string。 |
| vert       | 表示图形是纵向或者横向，默认为None。                         |
| positions  | 表示图形位置，接叫array，默认为None。                        |
| widths     | 接收scalar或者array，表示每个箱体的宽度，默认为None。        |
| labels     | 指定每一个箱线图的标签，默认为None。                         |
| meanline   | 表示是否显示均值线，接叫boolean，默认为False。               |
| showfliers | False：不显示超出Q0-Q5范围的点，True：显示超出Q0-Q5范围的点。 |
| whis=1.5   | Q0=Q1-whis*(Q3-Q1)，Q5=Q3+whis*(Q3-Q1)                       |

#### 举例1：

```python
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

fig, ax = plt.subplots()

# 准备数据
np.random.seed(2) # 固定随机生成的
df = pd.DataFrame(np.random.rand(5, 4), columns=['A', 'B', 'C', 'D'])

print(df)
"""
          A         B         C         D
0  0.435995  0.025926  0.549662  0.435322
1  0.420368  0.330335  0.204649  0.619271
2  0.299655  0.266827  0.621134  0.529142
3  0.134580  0.513578  0.184440  0.785335
4  0.853975  0.494237  0.846561  0.079645
"""
df.boxplot()
ax.boxplot(df, sym='r+', whis=1.25)
plt.show()
```

![image-20240819191245269](assets/image-20240819191245269.png)



## 柱状图

柱状图一般用横轴表示数据所属类别，纵轴表示数量或者占比。

柱状图主要功能是观察数据模式、样本的频率分布和总体的分布。

- 适用于比较产品质量特性的分布状态，判断其总体质量分布情况。
- 适用于属于不同组别的统计，可以用于比较组别的数据差异。

### **与直方图区别：**

1）直方图展示数据的分布，柱状图比较数据的大小。

2）直方图X轴为定量数据（数据区间），柱状图X轴为分类数据。

3）[直方图](https://so.csdn.net/so/search?q=直方图&spm=1001.2101.3001.7020)柱子无间隔（区间是连续的），柱状图柱子有间隔

4）直方图柱子宽度可不一，柱状图柱子宽度须一致

### **bar()函数**

| 函数接口                                                     | 函数作用   |
| :----------------------------------------------------------- | :--------- |
| matplotlib.pyplot.bar（x，left，height，width = 0.8，bottom = None，hold = None，data = None，** kwargs ） | 绘制柱状图 |

> **bar()函数绘制垂直柱状图，barh()函数绘制水平柱状图。**



### **基本参数说明：**

| 参数名称   | 说明                                                      |
| :--------- | :-------------------------------------------------------- |
| x          | bar坐标刻度参数。也是默认的坐标刻度文本。barh为y参数。    |
| left       | 每根柱子最左边的刻度，接受list。                          |
| height     | 每根柱子的高度，即y轴上的坐标。浮点数或类数组结构。       |
| width      | 柱子的宽度，接收0-1之间的float，如0.5。                   |
| bottom     | 柱子最低处的高度，浮点数或类数组结构。如0.5。             |
| color      | 柱子的颜色，颜色值或颜色值序列。如‘red’。                 |
| edgecolor  | 柱形图边缘颜色，颜色值或颜色值序列。                      |
| linewidth  | 柱形图边缘宽度，浮点数或类数组。如果为0，不绘制柱子边缘。 |
| tick_label | 坐标刻度文本，字符串或字符串列表。不再使用x,y作为柱标签。 |

### 举例1：

> 单个柱状图代码示例

```python
import numpy as np
import matplotlib.pyplot as plt

fig, ax = plt.subplots()

# 准备数据
x = np.arange(6)  # [0 1 2 3 4 5]

y = np.random.uniform(0, 10, 6)  # 生成一个含有6个元素值从0到10任意取的数组

ax.bar(x, y, tick_label=["dx", "dlne", 'fsx', 'xz', 'zsl','srz'], color='gray', lw=1, ec='orange')  # 制定文本作为标签，填充灰色，描边宽度为1，颜色为橙色

plt.show()
```

tick_label=["dx", "dlne", 'fsx', 'xz', 'zsl','srz']是代替x的数据

![image-20240819192304593](assets/image-20240819192304593.png)

### 举例2：

> 设置柱形图宽度，bar() 方法使用 width 设置，barh() 方法使用 height 设置 height
> 垂直柱状图与水平柱状图代码示例（两种作图方式）：

```python
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

fig, axes = plt.subplots(2, 1)
data = pd.Series(np.random.randn(16), index=list('abcdefghijklmnop'))
data.plot.bar(ax=axes[0], color='red', alpha=0.7)  # 绘制垂直柱状图
data.plot.barh(ax=axes[1], color='gray', alpha=0.7)  # 绘制水平柱状图

plt.show()
```

![image-20240819192703025](assets/image-20240819192703025.png)

不同写法

```python
import numpy as np
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(6.4, 4.8))
ax1 = fig.add_subplot(221)  # 211的2是第二行，第一个1是第一列，第二1是第几个
ax2 = fig.add_subplot(222)
ax3 = fig.add_subplot(223)
x1 = list('abcdefghijklmnop')
y1 = np.random.randn(16)

ax1.bar(x1, y1, color='red', alpha=0.7, width=0.5, )
ax2.barh(x1, y1, color='gray', alpha=0.7, height=0.5)
ax3.barh(x1, y1, color='yellow', alpha=0.7, height=0.5)

plt.show()
```

![image-20240819193610476](assets/image-20240819193610476.png)



### 举例3：

> 分组柱形图代码示例：



```python
import numpy as np
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(7, 5))

x = np.arange(1, 6)
Y1 = np.random.uniform(1.5, 1.0, 5)
Y2 = np.random.uniform(1.5, 1.0, 5)
ax.bar(x, Y1, width=0.35, facecolor='lightskyblue', edgecolor='white', label='A')
ax.bar(x + 0.35, Y2, width=0.35, facecolor='yellowgreen', edgecolor='red', label='B')
ax.legend()
plt.show()
```

![image-20240819193934697](assets/image-20240819193934697.png)

### 举例4：

> 分组柱形图（进阶）代码示例：

```python
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.patches as mpatches

matplotlib.rcParams['font.sans-serif'] = ['SimHei']
matplotlib.rcParams['axes.unicode_minus'] = False

plt.rcParams['xtick.direction'] = 'in'  # 显示在图里面（不写默认在外面）
plt.rcParams['ytick.direction'] = 'in'

fig, ax = plt.subplots(figsize=(10, 8), dpi=80)

year = ['2015年', '2016年', '2017年']
data1 = [68826, 73125, 77198]
data2 = [50391, 54455, 57892]

# 先得到year长度, 再得到下标组成列表
x = range(len(year))
ax.bar(x, data1, width=0.2, color='#FF6347')  # 这告诉我他是把x传到函数进行遍历

# 向右移动0.2，柱状条宽度为0.2
ax.bar([i + 0.2 for i in x], data2, width=0.2, color='#008B8B')

# 底部汉字移动到两个柱状条中间(本来汉字是在左边蓝色柱状条下面, 向右移动0.1)
ax.set_xticks([i + 0.1 for i in x])  # 设置字的位置
ax.set_xticklabels(year, fontsize=15)  # 设置字
ax.set_ylabel('人数（万人）', size=13)

color = ['#FF6347', '#008B8B']
labels = ['中国网民规模', '中国网络视频用户规模']

# 因为前面并没有设置标签，通过这个可以对各标签进行属性添加
patches = [mpatches.Patch(color=color[i], label="{}".format(labels[i])) for i in range(len(color))]

ax = plt.gca()  # 获取当前坐标对象
box = ax.get_position()
ax.set_position([box.x0, box.y0, box.width, box.height * 0.8])
# 下面一行中bbox_to_anchor指定了legend的位置; ncol = n 是一行允许放n个参数
ax.legend(handles=patches, bbox_to_anchor=(0.65, 1.12), ncol=2)  # 生成legend

# 为每个条形图添加数值标签
for x1, y1 in enumerate(data1):
    ax.text(x1, y1 + 200, str(y1), ha='center', fontsize=16)
for x2, y2 in enumerate(data2):
    ax.text(x2 + 0.2, y2 + 200, str(y2), ha='center', fontsize=16)
"""
1.text函数
text(x, y, s, fontdict=None, withdash=False, **kwargs)
x,y：注释的坐标位置（标量）
s：注释的内容（字符串）
fontdict：重新设置注释内容的文本格式，包括字体颜色、背景大小和颜色、字体大小等（字典）
withdash：创建一个替代注释内容“s”的对象，参照英文单词解释，这应该是一个破折号
其中比较常用的有:
va：垂直分布情况
ha：水平分布情况
bbox：给文字加上一个框子（例如：bbox=dict(facecolor=’red’, alpha=0.5)）
fontsize：字体大小
"""

plt.show()
```





## 饼图

饼图（Pie Graph）是将各项的大小与各项总和的比例显示在一张“饼”中，以“饼”的大小来确定每一项的占比。

饼图主要功能是反映出部分与部分、部分与整体之间的比例关系。

适用于要求直观地展示每组数据相对于总数的大小。



### **pie()函数**

| 函数接口                                                     | 函数作用 |
| :----------------------------------------------------------- | :------- |
| matplotlib.pyplot.pie(x, explode=None, labels=None, colors=None, autopct=None, pctdistance=0.6, shadow=False, labeldistance=1.1, startangle=0, radius=1, counterclock=True, wedgeprops=None, textprops=None, center=0, 0, frame=False, rotatelabels=False, *, normalize=None, data=None)[source] | 绘制饼图 |

### **基本参数说明：**

| 参数名称      | 说明                                                         |
| :------------ | :----------------------------------------------------------- |
| x             | 每一块饼的数值，浮点型数组或列表。                           |
| explode       | 每块饼突出来的距离，数组。如 explode=[0,0.2,0,0]：第i个数字表示第i个饼炸开距离（数值×半径=距离），explode参数的长度必须和x参数相同。 |
| labels        | 指定第一个饼图的文本标签，列表，默认为None。                 |
| colors        | 每块饼的颜色，数组，默认为None。                             |
| autopct       | 指定数值的显示方式，设置小数点，默认为None，%d%% 整数百分比，%0.1f 一位小数， %0.1f%% 一位小数百分比， %0.2f%% 两位小数百分。 |
| labeldistance | 标签标记的绘制位置，相对于半径的比例，默认为1.1，如<1则绘制在饼图内侧，float。 |
| pctdistance   | 类似于 labeldistance，指定 autopct 的位置刻度，默认值为 0.6。 |
| shadow        | 布尔值 True 或 False，是否有阴影，默认为 False，不设置阴影。 |
| radius        | 设置饼图的半径，默认为1。                                    |
| startangle    | 指定饼图的起始角度，默认从x轴正方向逆时针画起，如设定=90则从y轴正方向画起。 |
| counterclock  | 布尔值，用于指定是否逆时针绘制扇形，默认True，即逆时针绘制，False为顺时针。 |
| wedgeprops    | 字典类型，默认值 None。用于指定扇形的属性，比如边框线颜色、边框线宽度等，如 |
| textporps     | 字典类型， 用于指定文本标签的属性，比如字体大小、字体颜色等，默认值为None。 |
| center        | 浮点类型的列表，用于指定饼图的中心位置，默认值:(0,0)         |
| frame         | 布尔类型，用于指定是否绘制饼图的边框，默认值：False。如果是True，绘制带有表的轴框架。 |
| rotatelabels  | 布尔类型，指定是否旋转文本标签，默认为False。如果为True，旋转每个label到指定的角度。 |
| data          | 用于指定数据。如果设置了 data 参数，则可以直接使用数据框中的列作为 x、labels 等参数的值，无需再次传递。 |

#### 举例1：

```python
import numpy as np
import matplotlib.pyplot as plt

fig, ax = plt.subplots()

# 准备数据
labels = 'Spring', 'Summer', 'Autumn', 'Winter'
x = [15, 30, 45, 60]

ax.pie(x
       , labels=labels  # 设置标签
       , explode=[0, 0, 0.3, 0]  # 第3个饼图炸开
       , wedgeprops={'lw': 5, 'ec': 'red'}  # 描边:lw是边有多宽，ec是边的颜色
       , startangle=60  # 起始角度，默认从0开始逆时针转
       , autopct='%1.1f%%'  # 在图中显示比例值，注意值的格式。
       )

ax.set_title('Rany days by season')
plt.show()

```

![image-20240819214112459](assets/image-20240819214112459.png)



## **直方图**

直方图（Histogram）又称质量分布图，是统计报告图的一种，表示同一组数据在连续间隔或者特定时间段内容的分布情况，横轴表示数据类型（同一个变量的分组统计），纵轴表示一组数据分布情况，每个数据宽度可以任意变化。

直方图主要功能是描述一组数据的**频次分布**，有助于观察众数、中位数的位置，关注数据存在缺口或者异常值。



### **hist()函数**

| 函数接口                                                     | 函数作用   |
| :----------------------------------------------------------- | :--------- |
| matplotlib.pyplot.hist(x, bins=None, range=None, density=False, weights=None, cumulative=False, bottom=None, histtype=‘bar’, align=‘mid’, orientation=‘vertical’, rwidth=None, log=False, color=None, label=None, stacked=False, **kwargs) | 绘制直方图 |



### **基本参数说明：**

| 参数名称    | 说明                                                         |
| :---------- | :----------------------------------------------------------- |
| x           | 表示要绘制直方图的数据，可以是一个二元组或列表。             |
| bins        | 表示直方图的箱数，默认为10。                                 |
| range       | 表示要直方图的值域范围，可以是一个一维数组或列表。默认为None，即使用数据中的最小值和最大值。 |
| density     | 表示是否将直方图归一化，默认为False，即直方图的高度为每个箱子内的样本数，而不是频率或概率密度。 |
| weights     | 表示每个数据点的权重，默认为None。                           |
| cumulative  | 表示是否绘制累积分布图，默认为False。                        |
| bottom      | 表示直方图的起始高度，默认为None。                           |
| histtype    | 表示直方图类型，可以是’bar’、’barstacked’、‘step’、‘stepfilled’等。默认为’bar’。 |
| align       | 表示直方图箱子的对齐方式，可以是’left’、‘mid’、‘right’。默认为’mid’。 |
| orientation | 表示直方图的方向，可以是’vertical’、‘horizontal’。默认为’vertical’。 |
| rwidth      | 表示每个箱子的宽度。默认为None。                             |
| log         | 表示是否在y轴上使用对数刻度。默认为False。                   |
| color       | 表示直方图的颜色。                                           |
| label       | 表示直方图的标签。                                           |
| stacked     | 表示是否堆叠不同的直方图。默认为False。                      |
| **kwargs    | 表示其他绘图参数。                                           |



#### 举例1：

```python
import numpy as np
import matplotlib.pyplot as plt

fig, ax = plt.subplots()

# 使用 NumPy 生成随机数
x = np.random.normal(170, 10, 250)
ax.hist(x, bins=20, color='r', cumulative=True)
ax.set_xlabel('random test')

plt.show()
plt.close()
```

![image-20240819215042051](assets/image-20240819215042051.png)



## 可视化数据的分布情况

函数功能：判定数据(或特征)的分布情况
调用方法：

```python
plt.hist(x, bins=10, range=None, normed=False, weights=None, cumulative=False, bottom=None, histtype='bar', align='mid', orientation='vertical', rwidth=None, log=False, color=None, label=None, stacked=False)
```



- 参数说明：
  - x：指定要绘制直方图的数据；
  - bins：指定直方图条形的个数；
  - range：指定直方图数据的上下界，默认包含绘图数据的最大值和最小值；
  - density：是否将直方图的频数转换成频率；
  - weights：该参数可为每一个数据点设置权重；
  - cumulative：是否需要计算累计频数或频率；
  - bottom：可以为直方图的每个条形添加基准线，默认为0；
  - histtype：指定直方图的类型，默认为bar，除此还有’barstacked’, ‘step’, ‘stepfilled’；
  - align：设置条形边界值的对其方式，默认为mid，除此还有’left’和’right’；
  - orientation：设置直方图的摆放方向，默认为垂直方向；
  - rwidth：设置直方图条形宽度的百分比；
  - log：是否需要对绘图数据进行log变换；
  - color：设置直方图的填充色；
  - label：设置直方图的标签，可通过legend展示其图例；
  - stacked：当有多个数据时，是否需要将直方图呈堆叠摆放，默认水平摆放；
    



# 五、绘图数据和多子图绘图



## 数据绘图

### 列表绘图

```python
import matplotlib.pyplot as plt

x = [1, 2, 3, 4]
y = [1, 4, 9, 16]

plt.plot(x, y)

plt.show()
```

![image-20240819215554878](assets/image-20240819215554878.png)



### 数组

```python
import numpy as np 
import matplotlib.pyplot as plt
 
t=np.arange(0,5,0.2)
 
#线条1
x1=y1=t
 
#线条2
x2=x1
y2=t**2
#线条3
x3=x1
y3=t**3
 
#使用plot绘制线条
lineslist=plt.plot(x1,y1,x2,y2,x3,y3)
 
#使用setp方法可以同时设置多个线条的属性
plt.setp(lineslist,color='r')
plt.show()
 
print('',type(lineslist))
print('',len(lineslist))
```

![image-20240819220109618](assets/image-20240819220109618.png)



### 字典绘图

```python
import numpy as np
import matplotlib.pyplot as plt

data = {'x': np.arange(50),
        'y': np.random.randint(0, 50, 50),
        'color': np.random.randn(50)}

plt.scatter('x', 'y', c='color', data=data)

plt.show()
```

![image-20240819220225149](assets/image-20240819220225149.png)



## 多子图绘制

可以用如下三个方法创建多个子图

- fig,axes=plt.subplots(m,n)：一次生成m行n列子图。
- axes=fig.subplots(m,n)：一次生成m行n列子图，返回m*n个axes对象。
- fig.add_subplot((m,n,i))：增加一个子图，m行n列放在第i个。建议采用这个方法创建多子图。
  - 也可以fig.add_subplots(mni)方式调用。



### 举例1：

> fig.add_subplot((m,n,i))：增加一个子图，m行n列放在第i个。建议采用这个方法创建多子图。

```python
import numpy as np
import matplotlib.pyplot as plt

# 准备数据
x = np.linspace(-np.pi, np.pi, 100)
y = np.sin(x)

fig = plt.figure()
ax1 = fig.add_subplot(2, 2, 1)
ax1.plot(x, y, 'r-')
ax2 = fig.add_subplot(2, 2, 3)
ax2.plot(x, y, 'b:')
ax3 = fig.add_subplot(1, 2, 2)
ax3.plot(x, y, 'Dg--')

plt.show()
```

#### **画布级别标题和标签**

- fig.suptitle(“figtitle”)：设置总标题（与所有子图平级）
- fig.supxlabel(“figxlabel”)：设置总x标签（如果所有子图标签相同，可以只设置一个总标签）
- fig.supylabel(“figxlabel”)：设置总y标签

#### **子图间距**

- fig.subplots_adjust(wspace=0.5,hspace=0.5)：调整子图之间的间距
  - wspace：表示子图间宽度方向间隔系数
  - hspace：表示子图间高度方向间隔系数
- fig.tight_layout(pad=1)：调整子图四周空白宽度
  - pad：四周空白宽度系数
  - w_pad：宽度方向空白宽度系数
  - h_pad：高度方向空白宽度系数



添加其他属性

```python
import numpy as np
import matplotlib.pyplot as plt

# 准备数据
x = np.linspace(-np.pi, np.pi, 100)
y = np.sin(x)

fig = plt.figure()

# 画布设置
fig.suptitle("figtitle", x=0.5, y=0.98)
fig.supxlabel("figxlabel", x=0.5, y=0.02)
fig.supylabel("figylabel", x=0.02, y=0.5)
fig.subplots_adjust(wspace=0.5, hspace=0.5)
fig.tight_layout(pad=2)

ax1 = fig.add_subplot(2, 2, 1)
ax1.plot(x, y, 'r-')
ax2 = fig.add_subplot(2, 2, 3)
ax2.plot(x, y, 'b:')
ax3 = fig.add_subplot(1, 2, 2)
ax3.plot(x, y, 'Dg--')

plt.show()
```

![image-20240819221300496](assets/image-20240819221300496.png)





# 六、绘制三维图

创建`Axes3D`主要有方式，是利用关键字`projection='3d'`来实现，`fig.add_subplot(111, projection='3d')`



方法一

```python
from matplotlib import pyplot as plt

#定义坐标轴
fig = plt.figure()
ax1 = plt.axes(projection='3d')
plt.axis('equal')
plt.show()
```



方法二

```python
from matplotlib import pyplot as plt

# 定义坐标轴
fig = plt.figure()
ax = fig.add_subplot(111, projection='3d')  # 这种方法也可以画多个子图
plt.axis('equal')
plt.show()
```



## 三维曲面

```python
# 方法一，利用关键字
import numpy as np
from matplotlib import pyplot as plt

fig = plt.figure()  # 定义新的三维坐标轴
ax3 = plt.axes(projection='3d')

# 定义三维数据
xx = np.arange(-5, 5, 0.5)
yy = np.arange(-5, 5, 0.5)
X, Y = np.meshgrid(xx, yy)


Z = np.sin(X) + np.cos(Y) 

# 作图
ax3.plot_surface(X, Y, Z, cmap='rainbow')
# 等高线图，要设置offset就是等高线在那个地方展示并且，最小值为Z的最小值.这里的Z必须是二维数组，构造和上面类似
ax3.contour(X, Y, Z, offset=-2, cmap='rainbow')  

plt.show()
```





## 三维曲线和散点

```python
from matplotlib import pyplot as plt
import numpy as np

# 定义坐标轴
fig = plt.figure()
ax = fig.add_subplot(111, projection='3d')  # 这种方法也可以画多个子图

z = np.linspace(0, 13, 1000)
x = 5 * np.sin(z)
y = 5 * np.cos(z)
zd = 13 * np.random.random(100)
xd = 5 * np.sin(zd)
yd = 5 * np.cos(zd)
ax.scatter3D(xd, yd, zd, c='b')  # 绘制散点图
ax.plot3D(x, y, z, 'gray')  # 绘制空间曲线
plt.show()
```



# 七、imshow() 方法

imshow() 函数是 Matplotlib 库中的一个函数，用于显示图像。

imshow() 函数常用于绘制二维的灰度图像或彩色图像。

imshow() 函数可用于绘制矩阵、热力图、地图等。

imshow() 方法语法格式如下：

```python
imshow(X, cmap=None, norm=None, aspect=None, interpolation=None, alpha=None, vmin=None, vmax=None, origin=None, extent=None, shape=None, filternorm=1, filterrad=4.0, imlim=None, resample=None, url=None, *, data=None, **kwargs)

```

## **参数说明：**

- `X`：输入数据。可以是二维数组、三维数组、PIL图像对象、matplotlib路径对象等。
- `cmap`：颜色映射。用于控制图像中不同数值所对应的颜色。可以选择内置的颜色映射，如`gray`、`hot`、`jet`等，也可以自定义颜色映射。
- `norm`：用于控制数值的归一化方式。可以选择`Normalize`、`LogNorm`等归一化方法。
- `aspect`：控制图像纵横比（aspect ratio）。可以设置为`auto`或一个数字。
- `interpolation`：插值方法。用于控制图像的平滑程度和细节程度。可以选择`nearest`、`bilinear`、`bicubic`等插值方法。
- `alpha`：图像透明度。取值范围为0~1。
- `origin`：坐标轴原点的位置。可以设置为`upper`或`lower`。
- `extent`：控制显示的数据范围。可以设置为`[xmin, xmax, ymin, ymax]`。
- `vmin`、`vmax`：控制颜色映射的值域范围。
- `filternorm 和 filterrad`：用于图像滤波的对象。可以设置为`None`、`antigrain`、`freetype`等。
- `imlim`： 用于指定图像显示范围。
- `resample`：用于指定图像重采样方式。
- `url`：用于指定图像链接。

以下是一些 imshow() 函数的使用实例。



## 实例

### 显示灰度图像

```python
import matplotlib.pyplot as plt
import numpy as np

# 生成一个二维随机数组
img = np.random.rand(10, 10)

# 绘制灰度图像
plt.imshow(img, cmap='gray')

# 显示图像
plt.show()
```

以上实例中我们生成了一个 10x10 的随机数组，并使用 imshow() 函数将其显示为一张灰度图像。

我们设置了 cmap 参数为 gray，这意味着将使用灰度颜色映射显示图像。

显示结果如下：

![image-20241021221850030](assets/image-20241021221850030.png)

### 显示彩色图像

```python
import matplotlib.pyplot as plt
import numpy as np

# 生成一个随机的彩色图像
img = np.random.rand(10, 10, 3)

# 绘制彩色图像
plt.imshow(img)

# 显示图像
plt.show()
```

以上实例中我们生成了一个 10x10 的随机彩色图像，并使用 imshow() 函数将其显示出来。

由于彩色图像是三维数组，因此不需要设置 cmap 参数。

显示结果如下：

![image-20241021222114191](assets/image-20241021222114191.png)

### 显示热力图

```python
import matplotlib.pyplot as plt
import numpy as np

# 生成一个二维随机数组
data = np.random.rand(10, 10)

# 绘制热力图
plt.imshow(data, cmap='hot')

# 显示图像
plt.colorbar()
plt.show()
```

以上实例中我们生成了一个 10x10 的随机数组，并使用 imshow() 函数将其显示为热力图。

我们设置了 cmap 参数为 hot，这意味着将使用热度颜色映射显示图像。

此外，我们还添加了一个颜色条（colorbar），以便查看数据的值与颜色之间的关系。

显示结果如下：

![image-20241021222150145](assets/image-20241021222150145.png)

### 显示地图

```python
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

# 加载地图图像, 下载地址：https://static.jyshare.com/images/demo/map.jpeg
img = Image.open('./map.jpeg')

# 转换为数组
data = np.array(img)

# 绘制地图
plt.imshow(data)

# 隐藏坐标轴
plt.axis('off')

# 显示图像
plt.show()
```

以上实例中我们加载了一张地图图像，并将其转换为数组。

然后，我们使用 imshow() 函数将其显示出来，并使用 **axis('off')** 函数隐藏了坐标轴，以便更好地查看地图。

显示结果如下：

![image-20241021222330786](assets/image-20241021222330786.png)

### 显示矩阵

```python
import matplotlib.pyplot as plt
import numpy as np

# 生成一个随机矩阵
data = np.random.rand(10, 10)

# 绘制矩阵
plt.imshow(data)

# 显示图像
plt.show()
```

以上实例中我们生成了一个随机矩阵，并使用 imshow() 函数将其显示为一张图像。

由于矩阵也是二维数组，因此可以使用 imshow() 函数将其显示出来。

显示结果如下：

![image-20241021222851164](assets/image-20241021222851164.png)

## 更多实例

以下创建了一个 4x4 的二维 numpy 数组，并对其进行了三种不同的 imshow 图像展示。

- 第一张展示了灰度的色彩映射方式，并且没有进行颜色的混合（blending）。
- 第二张展示了使用viridis颜色映射的图像，同样没有进行颜色的混合。
- 第三张展示了使用viridis颜色映射的图像，并且使用了双立方插值方法进行颜色混合。

```python
import matplotlib.pyplot as plt
import numpy as np

n = 4

# 创建一个 n x n 的二维numpy数组
a = np.reshape(np.linspace(0,1,n**2), (n,n))

plt.figure(figsize=(12,4.5))

# 第一张图展示灰度的色彩映射方式，并且没有进行颜色的混合
plt.subplot(131)
plt.imshow(a, cmap='gray', interpolation='nearest')
plt.xticks(range(n))
plt.yticks(range(n))
# 灰度映射，无混合
plt.title('Gray color map, no blending', y=1.02, fontsize=12)

# 第二张图展示使用viridis颜色映射的图像，同样没有进行颜色的混合
plt.subplot(132)
plt.imshow(a, cmap='viridis', interpolation='nearest')
plt.yticks([])
plt.xticks(range(n))
# Viridis映射，无混合
plt.title('Viridis color map, no blending', y=1.02, fontsize=12)

# 第三张图展示使用viridis颜色映射的图像，并且使用了双立方插值方法进行颜色混合
plt.subplot(133)
plt.imshow(a, cmap='viridis', interpolation='bicubic')
plt.yticks([])
plt.xticks(range(n))
# Viridis 映射，双立方混合
plt.title('Viridis color map, bicubic blending', y=1.02, fontsize=12)

plt.show()
```

显示结果如下：

![image-20241021223037217](assets/image-20241021223037217.png)
