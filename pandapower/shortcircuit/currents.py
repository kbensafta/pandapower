# -*- coding: utf-8 -*-

# Copyright (c) 2016-2021 by University of Kassel and Fraunhofer Institute for Energy Economics
# and Energy System Technology (IEE), Kassel. All rights reserved.


import numpy as np
import pandas as pd

from pandapower.auxiliary import _sum_by_group
from pandapower.pypower.idx_bus import BASE_KV
from pandapower.pypower.idx_gen import GEN_BUS, MBASE
from pandapower.shortcircuit.idx_brch import IKSS_F, IKSS_T, IP_F, IP_T, ITH_F, ITH_T
from pandapower.shortcircuit.idx_bus import C_MIN, C_MAX, KAPPA, R_EQUIV, IKSS1, IP, ITH,\
    X_EQUIV, IKSS2, IKCV, M, R_EQUIV_OHM, X_EQUIV_OHM, V_G, K_SG, SKSS
from pandapower.shortcircuit.impedance import _calc_zbus_diag

from pandapower.pypower.pfsoln import pfsoln as pfsoln_pypower
from pandapower.pf.ppci_variables import _get_pf_variables_from_ppci


def _calc_ikss(net, ppc, ppci_bus=None):
    # Vectorized for multiple bus
    if ppci_bus is None:
        # Slice(None) is equal to : select
        bus_idx = np.arange(ppc["bus"].shape[0])
    else:
        bus_idx = ppci_bus
        # bus_idx = net._pd2ppc_lookups["bus"][bus] #bus where the short-circuit is calculated (j)

    fault = net._options["fault"]
    case = net._options["case"]
    c = ppc["bus"][bus_idx, C_MIN] if case == "min" else ppc["bus"][bus_idx, C_MAX]
    ppc["internal"]["baseI"] = ppc["bus"][:, BASE_KV] * np.sqrt(3) / ppc["baseMVA"]
    
    # Only for test, should correspondant to PF result
    baseZ = ppc["bus"][bus_idx, BASE_KV] ** 2 / ppc["baseMVA"]
    ppc["bus"][bus_idx, R_EQUIV_OHM] = baseZ * ppc["bus"][bus_idx, R_EQUIV]
    ppc["bus"][bus_idx, X_EQUIV_OHM] = baseZ * ppc["bus"][bus_idx, X_EQUIV]

    z_equiv = abs(ppc["bus"][bus_idx, R_EQUIV] + ppc["bus"][bus_idx, X_EQUIV] * 1j)
    if fault == "3ph":
        ppc["bus"][bus_idx, IKSS1] = c / z_equiv / ppc["bus"][bus_idx, BASE_KV] / np.sqrt(3) * ppc["baseMVA"]
    elif fault == "2ph":
        ppc["bus"][bus_idx, IKSS1] = c / z_equiv / ppc["bus"][bus_idx, BASE_KV] / 2 * ppc["baseMVA"]

    if fault == "3ph":
        ppc["bus"][bus_idx, SKSS] = np.sqrt(3) * ppc["bus"][bus_idx, IKSS1] * ppc["bus"][bus_idx, BASE_KV] 
    elif fault == "2ph":
        ppc["bus"][bus_idx, SKSS] = ppc["bus"][bus_idx, IKSS1] * ppc["bus"][bus_idx, BASE_KV] / np.sqrt(3)

    # Correct voltage of generator bus inside power station
    if np.any(~np.isnan(ppc["bus"][:, K_SG])):
        gen_bus_idx = bus_idx[~np.isnan(ppc["bus"][bus_idx, K_SG])]
        ppc["bus"][gen_bus_idx, IKSS1] *=\
            (ppc["bus"][gen_bus_idx, V_G] / ppc["bus"][gen_bus_idx, BASE_KV])
        ppc["bus"][gen_bus_idx, SKSS] *=\
            (ppc["bus"][gen_bus_idx, V_G] / ppc["bus"][gen_bus_idx, BASE_KV])

    _current_source_current(net, ppc)
    

def _calc_ikss_1ph(net, ppc, ppc_0, ppci_bus=None):
    # Vectorized for multiple bus
    if ppci_bus is None:
        # Slice(None) is equal to : select
        bus_idx = np.arange(ppc["bus"].shape[0])
    else:
        bus_idx = ppci_bus

    case = net._options["case"]
    c = ppc["bus"][bus_idx, C_MIN] if case == "min" else ppc["bus"][bus_idx, C_MAX]
    ppc["internal"]["baseI"] = ppc["bus"][:, BASE_KV] * np.sqrt(3) / ppc["baseMVA"]
    ppc_0["internal"]["baseI"] = ppc_0["bus"][:, BASE_KV] * np.sqrt(3) / ppc_0["baseMVA"]

    z_equiv = abs((ppc["bus"][bus_idx, R_EQUIV] + ppc["bus"][bus_idx, X_EQUIV] * 1j) * 2 +
                  (ppc_0["bus"][bus_idx, R_EQUIV] + ppc_0["bus"][bus_idx, X_EQUIV] * 1j))

    ppc_0["bus"][bus_idx, IKSS1] = c / z_equiv / ppc_0["bus"][bus_idx, BASE_KV] * np.sqrt(3) * ppc_0["baseMVA"]
    ppc["bus"][bus_idx, IKSS1] = c / z_equiv / ppc["bus"][bus_idx, BASE_KV] * np.sqrt(3) * ppc["baseMVA"]
    _current_source_current(net, ppc)


def _current_source_current(net, ppc):
    ppc["bus"][:, IKCV] = 0
    ppc["bus"][:, IKSS2] = 0
    bus_lookup = net["_pd2ppc_lookups"]["bus"]
    if not False in net.sgen.current_source.values:
        sgen = net.sgen[net._is_elements["sgen"]]
    else:
        sgen = net.sgen[net._is_elements["sgen"] & net.sgen.current_source]
    if len(sgen) == 0:
        return
    if any(pd.isnull(sgen.sn_mva)):
        raise ValueError("sn_mva needs to be specified for all sgens in net.sgen.sn_mva")
    baseI = ppc["internal"]["baseI"]
    sgen_buses = sgen.bus.values
    sgen_buses_ppc = bus_lookup[sgen_buses]
    if not "k" in sgen:
        raise ValueError("Nominal to short-circuit current has to specified in net.sgen.k")
    i_sgen_pu = sgen.sn_mva.values / net.sn_mva * sgen.k.values
    buses, ikcv_pu, _ = _sum_by_group(sgen_buses_ppc, i_sgen_pu, i_sgen_pu)
    ppc["bus"][buses, IKCV] = ikcv_pu
    if net["_options"]["inverse_y"]:
        Zbus = ppc["internal"]["Zbus"]
        ppc["bus"][:, IKSS2] = abs(1 / np.diag(Zbus) * np.dot(Zbus, ppc["bus"][:, IKCV] * -1j) / baseI)
    else:
        ybus_fact = ppc["internal"]["ybus_fact"]
        diagZ = _calc_zbus_diag(net, ppc)
        ppc["bus"][:, IKSS2] = abs(ybus_fact(ppc["bus"][:, IKCV] * -1j) / diagZ / baseI)
    ppc["bus"][buses, IKCV] /= baseI[buses]


def _calc_ip(net, ppc):
    ip = np.sqrt(2) * (ppc["bus"][:, KAPPA] * ppc["bus"][:, IKSS1] + ppc["bus"][:, IKSS2])
    ppc["bus"][:, IP] = ip


def _calc_ith(net, ppc):
    tk_s = net["_options"]["tk_s"]
    kappa = ppc["bus"][:, KAPPA]
    f = 50
    n = 1
    m = (np.exp(4 * f * tk_s * np.log(kappa - 1)) - 1) / (2 * f * tk_s * np.log(kappa - 1))
    m[np.where(kappa > 1.99)] = 0
    ppc["bus"][:, M] = m
    ith = (ppc["bus"][:, IKSS1] + ppc["bus"][:, IKSS2]) * np.sqrt(m + n)
    ppc["bus"][:, ITH] = ith


def _calc_ib(net, ppci):
    Zbus = ppci["internal"]["Zbus"]
    baseI = ppci["internal"]["baseI"]
    tk_s = net._options['tk_s']
    c = 1.1

    z_equiv = ppci["bus"][:, R_EQUIV] + ppci["bus"][:, X_EQUIV] * 1j
    I_ikss = c / z_equiv / ppci["bus"][:, BASE_KV] / np.sqrt(3) * ppci["baseMVA"]

    # calculate voltage source branch current
    # I_ikss = ppci["bus"][:, IKSS1]
    # V_ikss = (I_ikss * baseI) * Zbus

    gen = net["gen"][net._is_elements["gen"]]
    gen_vn_kv = gen.vn_kv.values

    # Check difference ext_grid and gen
    gen_buses = ppci['gen'][:, GEN_BUS].astype(np.int64)
    gen_mbase = ppci['gen'][:, MBASE]
    gen_i_rg = gen_mbase / (np.sqrt(3) * gen_vn_kv)

    gen_buses_ppc, gen_sn_mva, I_rG = _sum_by_group(gen_buses, gen_mbase, gen_i_rg)

    # shunt admittance of generator buses and generator short circuit current
    # YS = ppci["bus"][gen_buses_ppc, GS] + ppci["bus"][gen_buses_ppc, BS] * 1j
    # I_kG = V_ikss.T[:, gen_buses_ppc] * YS / baseI[gen_buses_ppc]

    xdss_pu = gen.xdss_pu.values
    rdss_pu = gen.rdss_pu.values
    cosphi = gen.cos_phi.values
    X_dsss = xdss_pu * np.square(gen_vn_kv) / gen_mbase
    R_dsss = rdss_pu * np.square(gen_vn_kv) / gen_mbase

    K_G = ppci['bus'][gen_buses, BASE_KV] / gen_vn_kv * c / (1 + xdss_pu * np.sin(np.arccos(cosphi)))
    Z_G = (R_dsss + 1j * X_dsss)

    I_kG = c * ppci['bus'][gen_buses, BASE_KV] / np.sqrt(3) / (Z_G * K_G) * ppci["baseMVA"]

    dV_G = 1j * X_dsss * K_G * I_kG
    V_Is = c * ppci['bus'][gen_buses, BASE_KV] / np.sqrt(3)

    # I_kG_contribution = I_kG.sum(axis=1)
    # ratio_SG_ikss = I_kG_contribution / I_ikss
    # close_to_SG = ratio_SG_ikss > 5e-2

    close_to_SG = I_kG / I_rG > 2

    if tk_s == 2e-2:
        mu = 0.84 + 0.26 * np.exp(-0.26 * abs(I_kG) / I_rG)
    elif tk_s == 5e-2:
        mu = 0.71 + 0.51 * np.exp(-0.3 * abs(I_kG) / I_rG)
    elif tk_s == 10e-2:
        mu = 0.62 + 0.72 * np.exp(-0.32 * abs(I_kG) / I_rG)
    elif tk_s >= 25e-2:
        mu = 0.56 + 0.94 * np.exp(-0.38 * abs(I_kG) / I_rG)
    else:
        raise UserWarning('not implemented for other tk_s than 20ms, 50ms, 100ms and >=250ms')

    mu = np.clip(mu, 0, 1)

    I_ikss_G = abs(I_ikss - np.sum((1 - mu) * I_kG, axis=1))

    # I_ikss_G = I_ikss - np.sum(abs(V_ikss.T[:, gen_buses_ppc]) * (1-mu) * I_kG, axis=1)

    I_ikss_G = abs(I_ikss - np.sum(dV_G / V_Is * (1 - mu) * I_kG, axis=1))

    return I_ikss_G


def calc_branch_results(net, ppci, V):
    Ybus = ppci["internal"]["Ybus"]
    Yf = ppci["internal"]["Yf"]
    Yt = ppci["internal"]["Yt"]
    baseMVA, bus, gen, branch, ref, _, _, _, _, _, ref_gens = _get_pf_variables_from_ppci(ppci)
    bus, gen, branch = pfsoln_pypower(baseMVA, bus, gen, branch, Ybus, Yf, Yt, V, ref, ref_gens)
    ppci["bus"], ppci["gen"], ppci["branch"] = bus, gen, branch


def _calc_branch_currents(net, ppc, ppci_bus):
    n_sc_bus = np.shape(ppci_bus)[0]

    case = net._options["case"]
    minmax = np.nanmin if case == "min" else np.nanmax

    Yf = ppc["internal"]["Yf"]
    Yt = ppc["internal"]["Yt"]
    baseI = ppc["internal"]["baseI"]
    n_bus = ppc["bus"].shape[0]
    fb = np.real(ppc["branch"][:, 0]).astype(int)
    tb = np.real(ppc["branch"][:, 1]).astype(int)

    # calculate voltage source branch current
    if net["_options"]["inverse_y"]:
        Zbus = ppc["internal"]["Zbus"]
        V_ikss = (ppc["bus"][:, IKSS1] * baseI) * Zbus
        V_ikss = V_ikss[:, ppci_bus]
    else:
        ybus_fact = ppc["internal"]["ybus_fact"]
        # TODO: Check v_ikss size
        V_ikss = np.zeros((n_bus, n_sc_bus), dtype=np.complex)
        for ix, b in enumerate(ppci_bus):
            ikss = np.zeros(n_bus, dtype=np.complex)
            ikss[b] = ppc["bus"][b, IKSS1] * baseI[b]
            V_ikss[:, ix] = ybus_fact(ikss)

    ikss1_all_f = np.conj(Yf.dot(V_ikss))
    ikss1_all_t = np.conj(Yt.dot(V_ikss))
    ikss1_all_f[abs(ikss1_all_f) < 1e-10] = 0.
    ikss1_all_t[abs(ikss1_all_t) < 1e-10] = 0.

    # add current source branch current if there is one
    current_sources = any(ppc["bus"][:, IKCV]) > 0
    if current_sources:
        current = np.tile(-ppc["bus"][:, IKCV], (n_sc_bus, 1))
        for ix, b in enumerate(ppci_bus):
            current[ix, b] += ppc["bus"][b, IKSS2]

        # calculate voltage source branch current
        if net["_options"]["inverse_y"]:
            Zbus = ppc["internal"]["Zbus"]
            V = np.dot((current * baseI), Zbus).T
        else:
            ybus_fact = ppc["internal"]["ybus_fact"]
            V = np.zeros((n_bus, n_sc_bus), dtype=np.complex)
            for ix, b in enumerate(ppci_bus):
                V[:, ix] = ybus_fact(current[ix, :] * baseI[b])

        fb = np.real(ppc["branch"][:, 0]).astype(int)
        tb = np.real(ppc["branch"][:, 1]).astype(int)
        ikss2_all_f = np.conj(Yf.dot(V))
        ikss2_all_t = np.conj(Yt.dot(V))

        ikss_all_f = abs(ikss1_all_f + ikss2_all_f)
        ikss_all_t = abs(ikss1_all_t + ikss2_all_t)
    else:
        ikss_all_f = abs(ikss1_all_f)
        ikss_all_t = abs(ikss1_all_t)

    if net._options["return_all_currents"]:
        ppc["internal"]["branch_ikss_f"] = ikss_all_f / baseI[fb, None]
        ppc["internal"]["branch_ikss_t"] = ikss_all_t / baseI[tb, None]
    else:
        ikss_all_f[abs(ikss_all_f) < 1e-10] = np.nan
        ikss_all_t[abs(ikss_all_t) < 1e-10] = np.nan
        ppc["branch"][:, IKSS_F] = np.nan_to_num(minmax(ikss_all_f, axis=1) / baseI[fb])
        ppc["branch"][:, IKSS_T] = np.nan_to_num(minmax(ikss_all_t, axis=1) / baseI[tb])

    if net._options["ip"]:
        kappa = ppc["bus"][:, KAPPA]
        if current_sources:
            ip_all_f = np.sqrt(2) * (ikss1_all_f * kappa[ppci_bus] + ikss2_all_f)
            ip_all_t = np.sqrt(2) * (ikss1_all_t * kappa[ppci_bus] + ikss2_all_t)
        else:
            ip_all_f = np.sqrt(2) * ikss1_all_f * kappa[ppci_bus]
            ip_all_t = np.sqrt(2) * ikss1_all_t * kappa[ppci_bus]

        if net._options["return_all_currents"]:
            ppc["internal"]["branch_ip_f"] = abs(ip_all_f) / baseI[fb, None]
            ppc["internal"]["branch_ip_t"] = abs(ip_all_t) / baseI[tb, None]
        else:
            ip_all_f[abs(ip_all_f) < 1e-10] = np.nan
            ip_all_t[abs(ip_all_t) < 1e-10] = np.nan
            ppc["branch"][:, IP_F] = np.nan_to_num(minmax(abs(ip_all_f), axis=1) / baseI[fb])
            ppc["branch"][:, IP_T] = np.nan_to_num(minmax(abs(ip_all_t), axis=1) / baseI[tb])

    if net._options["ith"]:
        n = 1
        m = ppc["bus"][ppci_bus, M]
        ith_all_f = ikss_all_f * np.sqrt(m + n)
        ith_all_t = ikss_all_t * np.sqrt(m + n)

        if net._options["return_all_currents"]:
            ppc["internal"]["branch_ith_f"] = ith_all_f / baseI[fb, None]
            ppc["internal"]["branch_ith_t"] = ith_all_t / baseI[tb, None]
        else:
            ppc["branch"][:, ITH_F] = np.nan_to_num(minmax(ith_all_f, axis=1) / baseI[fb])
            ppc["branch"][:, ITH_T] = np.nan_to_num(minmax(ith_all_t, axis=1) / baseI[fb])
