import numpy as np

def extract_dpv_data(curve_object, cycle_time, pulse_time):
    # 1. Retrieve the NumPy table from the CpivCurve object
    # Columns: 0:Index, 1:Time, 2:Potential, 3:Current
    data = curve_object.acq_data()
    
    t = data[:, 1]  # Time column
    v = data[:, 2]  # Potential column
    i = data[:, 3]  # Current column
    
    base_time = cycle_time - pulse_time
    num_cycles = int(t[-1] // cycle_time)
    
    results = []

    for n in range(num_cycles):
        # Define the absolute time boundaries for this cycle
        t_start = n * cycle_time
        t_pulse_start = t_start + base_time
        t_end = (n + 1) * cycle_time
        
        # 2. Identify indices for the end of the Base period (A)
        # We look for points just before the pulse starts
        base_pts = np.where((t >= t_start) & (t < t_pulse_start))[0]
        
        # 3. Identify indices for the end of the Pulse period (B)
        pulse_pts = np.where((t >= t_pulse_start) & (t <= t_end))[0]
        
        # Check if we have at least 3 points in both regions to satisfy the average
        if len(base_pts) >= 3 and len(pulse_pts) >= 3:
            # Average the last three current values of the base period
            i_base_avg = np.mean(i[base_pts[-3:]])
            
            # Average the last three current values of the pulse period
            i_pulse_avg = np.mean(i[pulse_pts[-3:]])
            
            # Calculate Differential Current: Δi = i_pulse - i_base
            delta_i = i_pulse_avg - i_base_avg
            
            # Use the potential at the end of the base period as our Voltage coordinate
            v_base = v[base_pts[-1]]
            
            results.append([v_base, delta_i])
            
    return np.array(results)

# Usage with your variables
dpv_results = extract_dpv_data(my_curve, cycle_time=0.5, pulse_time=0.1)