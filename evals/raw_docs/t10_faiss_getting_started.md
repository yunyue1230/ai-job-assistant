For the following, we assume Faiss is installed. We provide code examples in C++<!--, Lua --> and Python. The code can be run by copy/pasting it or running it from the [`tutorial/`](https://github.com/facebookresearch/faiss/tree/master/tutorial) subdirectory of the Faiss distribution.

## Getting some data

Faiss handles collections of vectors of a fixed dimensionality d, typically a few 10s to 100s. These collections can be stored in matrices. We assume row-major storage, ie. the j'th component of vector number i is stored in row i, column j of the matrix. Faiss uses only 32-bit floating point matrices.

We need two matrices:

 - `xb` for the database, that contains all the vectors that must be indexed, and that we are going to search in. Its size is nb-by-d
 - `xq` for the query vectors, for which we need to find the nearest neighbors. Its size is nq-by-d. If we have a single query vector, nq=1.

In the following examples we are going to work with vectors that are drawn form a uniform distribution in d=64 dimensions. Just for fun, we add small translation along the first dimension that depends on the vector index.

### In Python

```python
import numpy as np
d = 64                           # dimension
nb = 100000                      # database size
nq = 10000                       # nb of queries
np.random.seed(1234)             # make reproducible
xb = np.random.random((nb, d)).astype('float32')
xb[:, 0] += np.arange(nb) / 1000.
xq = np.random.random((nq, d)).astype('float32')
xq[:, 0] += np.arange(nq) / 1000.
```
In Python, the matrices are always represented as numpy arrays.
The data type `dtype` must be `float32`.

<!--
### In Lua

```lua
  d = 64                           -- dimension
  nb = 100000                      -- database size
  nq = 10000                       -- nb of queries
  torch.manualSeed(1234)           -- make reproducible
  xb = torch.FloatTensor(nb, d):uniform()
  xb:select(2, 1):add((torch.range(1, nb) / 1000):float());
  xq = torch.FloatTensor(nq, d):uniform()
  xq:select(2, 1):add((torch.range(1, nq) / 1000):float());
```
-->

### In C++

```c++
    int d = 64;                            // dimension
    int nb = 100000;                       // database size
    int nq = 10000;                        // nb of queries
    float *xb = new float[d * nb];
    float *xq = new float[d * nq];
    for(int i = 0; i < nb; i++) {
        for(int j = 0; j < d; j++) xb[d * i + j] = drand48();
        xb[d * i] += i / 1000.;
    }
    for(int i = 0; i < nq; i++) {
        for(int j = 0; j < d; j++) xq[d * i + j] = drand48();
        xq[d * i] += i / 1000.;
    }
```

This example uses plain arrays, because this is the lowest common denominator all C++ matrix libraries support. Faiss can accommodate any matrix library, provided it provides a pointer to the underlying data. For example `std::vector<float>`'s internal pointer is given by the `data()` method.


## Building an index and adding the vectors to it

Faiss is built around the `Index` object. It encapsulates the set of database vectors, and optionally preprocesses them to make searching efficient. There are many types of indexes, we are going to use the simplest version that just performs brute-force L2 distance search on them: `IndexFlatL2`.

All indexes need to know when they are built which is the dimensionality of the vectors they operate on, `d` in our case. Then, most of the indexes also require a training phase, to analyze the distribution of the vectors. For `IndexFlatL2`, we can skip this operation.

When the index is built and trained, two operations can be performed on the index: `add` and `search`.

To add elements to the index, we call `add` on `xb`. We can also display the two state variables of the index: `is_trained`, a boolean that indicates whether training is required and `ntotal`, the number of indexed vectors.

Some indexes can also store integer IDs corresponding to each of the vectors (but not `IndexFlatL2`). If no IDs are provided, `add` just uses the vector ordinal as the id, ie. the first vector gets 0, the second 1, etc.

### In Python

```python
import faiss                   # make faiss available
index = faiss.IndexFlatL2(d)   # build the index
print(index.is_trained)
index.add(xb)                  # add vectors to the index
print(index.ntotal)
```

<!--
### In Lua

```lua
  faiss = require 'faiss'
  index = faiss.IndexFlatL2(d)  -- build the index
  print(index.is_trained)
  index:add(xb)                 -- add vectors to the index
  print(index.ntotal)
```
-->

### In C++

```c++
    faiss::IndexFlatL2 index(d);           // call constructor
    printf("is_trained = %s\n", index.is_trained ? "true" : "false");
    index.add(nb, xb);                     // add vectors to the index
    printf("ntotal = %ld\n", index.ntotal);
```

### Results

This should just display true (the index is trained) and 100000 (vectors are stored in the index).

## Searching

The basic search operation that can be performed on an index is the `k`-nearest-neighbor search, ie. for each query vector, find its `k` nearest neighbors in the database.

The result of this operation can be conveniently stored in an integer matrix of size `nq`-by-`k`, where row i contains the IDs of the neighbors of query vector i, sorted by increasing distance. In addition to this matrix, the `search` operation returns a `nq`-by-`k` floating-point matrix with the corresponding squared distances.

As a sanity check, we can first search a few database vectors, to make sure the nearest neighbor is indeed the vector itself.

### In Python

```python
k = 4                          # we want to see 4 nearest neighbors
D, I = index.search(xb[:5], k) # sanity check
print(I)
print(D)
D, I = index.search(xq, k)     # actual search
print(I[:5])                   # neighbors of the 5 first queries
print(I[-5:])                  # neighbors of the 5 last queries
```

<!--
### In Lua

```lua
  k = 4                          -- we want to see 4 nearest neighbors
  D, I = index:search(xb:sub(1, 5), k) -- sanity check
  print(I)
  print(D)
  D, I = index:search(xq, k)     -- actual search
  print(I:sub(1, 5))             -- neighbors of the 5 first queries
  print(I:sub(-5, -1))           -- neighbors of the 5 last queries
```
-->
### In C++

```c++
    int k = 4;
    {       // sanity check: search 5 first vectors of xb
        idx_t *I = new idx_t[k * 5];
        float *D = new float[k * 5];
        index.search(5, xb, k, D, I);
        printf("I=\n");
        for(int i = 0; i < 5; i++) {
            for(int j = 0; j < k; j++) printf("%5ld ", I[i * k + j]);
            printf("\n");
        }
        ...
        delete [] I;
        delete [] D;
    }
    {       // search xq
        idx_t *I = new idx_t[k * nq];
        float *D = new float[k * nq];
        index.search(nq, xq, k, D, I);
        ...
    }
```

The extract is edited because otherwise the C++ version becomes very verbose, see the full code in the [`tutorial/cpp`](https://github.com/facebookresearch/faiss/tree/master/tutorial/cpp) subdirectory of Faiss.

### Results

The output of the sanity check should look like

```
[[  0 393 363  78]
 [  1 555 277 364]
 [  2 304 101  13]
 [  3 173  18 182]
 [  4 288 370 531]]

[[ 0.          7.17517328  7.2076292   7.25116253]
 [ 0.          6.32356453  6.6845808   6.79994535]
 [ 0.          5.79640865  6.39173603  7.28151226]
 [ 0.          7.27790546  7.52798653  7.66284657]
 [ 0.          6.76380348  7.29512024  7.36881447]]
```

ie. the nearest neighbor of each query is indeed the index of the vector, and the corresponding distance is 0. And within a row, distances are increasing.<!-- Note that in Lua, indexing starts at 1, so the first column of the matrix will have an added 1. -->

The output of the actual search is similar to

```
[[ 381  207  210  477]
 [ 526  911  142   72]
 [ 838  527 1290  425]
 [ 196  184  164  359]
 [ 526  377  120  425]]

[[ 9900 10500  9309  9831]
 [11055 10895 10812 11321]
 [11353 11103 10164  9787]
 [10571 10664 10632  9638]
 [ 9628  9554 10036  9582]]
```

Because of the value added to the first component of the vectors, the dataset is smeared along the first axis in d-dim space. So the neighbors of the first few vectors are around the beginning of the dataset, and the ones of the vectors around ~10000 are also around index 10000 in the dataset.

Executing the search above takes about 3.3s on a 2016 machine.