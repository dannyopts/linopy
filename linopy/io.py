#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Module containing all import/export functionalities."""
import logging
import os
import time
from functools import partial, reduce

import numpy as np
import xarray as xr
from xarray import apply_ufunc

logger = logging.getLogger(__name__)


ufunc_kwargs = dict(dask="parallelized", vectorize=True, output_dtypes=[object])

# IO functions
def to_float_str(da):
    """Convert a float array to a string array with lp like format for coefficients."""
    return apply_ufunc(lambda f: "%+f" % f, da.fillna(0), **ufunc_kwargs)


def to_int_str(da, nonnans=None):
    """Convert a int array to a string array."""
    return xr.apply_ufunc(lambda d: "%d" % d, da.fillna(0), **ufunc_kwargs)


def join_str_arrays(arraylist):
    """Join string array together (elementwise concatenation of strings)."""
    func = partial(np.add, dtype=object)  # np.core.defchararray.add
    return reduce(func, arraylist, "")


def str_array_to_file(array, fn):
    """Elementwise writing out string values to a file."""
    return xr.apply_ufunc(
        lambda x: fn.write(x),
        array,
        dask="parallelized",
        vectorize=True,
        output_dtypes=[int],
    )


def objective_to_file(m, f):
    """Write out the objective of a model to a lp file."""
    f.write("min\nobj:\n")
    coef = m.objective.coeffs
    var = m.objective.vars

    nonnans = coef.notnull() & (var != -1)
    join = [to_float_str(coef), " x", to_int_str(var), "\n"]
    objective_str = join_str_arrays(join).where(nonnans, "")
    str_array_to_file(objective_str, f).compute()


def constraints_to_file(m, f):
    """Write out the constraints of a model to a lp file."""
    f.write("\n\ns.t.\n\n")
    con = m.constraints
    coef = m.constraints_lhs_coeffs
    var = m.constraints_lhs_vars
    sign = m.constraints_sign
    rhs = m.constraints_rhs

    term_names = [f"{n}_term" for n in con]

    nonnans = coef.notnull() & (var != -1)
    join = [to_float_str(coef), " x", to_int_str(var), "\n"]
    lhs_str = join_str_arrays(join).where(nonnans, "").reduce(np.sum, term_names)
    # .sum() does not work

    nonnans = nonnans.any(term_names) & (con != -1) & sign.notnull() & rhs.notnull()

    join = [
        "c",
        to_int_str(con),
        ": \n",
        lhs_str,
        sign,
        "\n",
        to_float_str(rhs),
        "\n\n",
    ]
    constraints_str = join_str_arrays(join).where(nonnans, "")
    str_array_to_file(constraints_str, f).compute()


def bounds_to_file(m, f):
    """Write out variables of a model to a lp file."""
    f.write("\nbounds\n")
    lb = m.variables_lower_bound[m._non_binary_variables]
    v = m.variables[m._non_binary_variables]
    ub = m.variables_upper_bound[m._non_binary_variables]

    nonnans = lb.notnull() & ub.notnull() & (v != -1)
    join = [to_float_str(lb), " <= x", to_int_str(v), " <= ", to_float_str(ub), "\n"]
    bounds_str = join_str_arrays(join).where(nonnans, "")
    str_array_to_file(bounds_str, f).compute()


def binaries_to_file(m, f):
    """Write out binaries of a model to a lp file."""
    f.write("\nbinary\n")

    v = m.binaries
    nonnans = v != -1
    binaries_str = join_str_arrays(["x", to_int_str(m.binaries), "\n"]).where(
        nonnans, ""
    )
    str_array_to_file(binaries_str, f).compute()
    f.write("end\n")


def to_file(m, fn):
    """Write out a model to a lp file."""
    if os.path.exists(fn):
        os.remove(fn)  # ensure a clear file

    with open(fn, mode="w") as f:

        start = time.time()

        objective_to_file(m, f)
        constraints_to_file(m, f)
        bounds_to_file(m, f)
        binaries_to_file(m, f)

        logger.info(f" Writing time: {round(time.time()-start, 2)}s")


def to_netcdf(m, *args, **kwargs):
    """
    Write out the model to a netcdf file.

    Parameters
    ----------
    m : linopy.Model
        Model to write out.
    *args
        Arguments passed to ``xarray.Dataset.to_netcdf``.
    **kwargs : TYPE
        Keyword arguments passed to ``xarray.Dataset.to_netcdf``.

    """
    from .model import array_attrs, obj_attrs  # avoid cyclic imports

    def get_and_rename(m, attr):
        ds = getattr(m, attr)
        return ds.rename({v: attr + "-" + v for v in ds})

    ds = xr.merge([get_and_rename(m, d) for d in array_attrs])
    ds = ds.assign_attrs({k: getattr(m, k) for k in obj_attrs})

    ds.to_netcdf(*args, **kwargs)


def read_netcdf(path, **kwargs):
    """
    Read in a model from a netcdf file.

    Parameters
    ----------
    path : path_like
        Path of the stored model.
    **kwargs
        Keyword arguments passed to ``xarray.load_dataset``.

    Returns
    -------
    m : linopy.Model

    """
    from .model import (  # avoid cyclic imports
        LinearExpression,
        Model,
        array_attrs,
        obj_attrs,
    )

    m = Model()
    all_ds = xr.load_dataset(path, **kwargs)

    for attr in array_attrs:
        keys = [k for k in all_ds if k.startswith(attr + "-")]
        ds = all_ds[keys].rename({k: k[len(attr) + 1 :] for k in keys})
        setattr(m, attr, ds)
    m.objective = LinearExpression(m.objective)

    for k in obj_attrs:
        setattr(m, k, ds.attrs.pop(k))

    return m
