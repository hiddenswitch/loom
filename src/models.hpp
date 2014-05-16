#pragma once

#include <type_traits>
#include <distributions/mixture.hpp>
#include <distributions/clustering.hpp>
#include <distributions/models/dd.hpp>
#include <distributions/models/dpd.hpp>
#include <distributions/models/nich.hpp>
#include <distributions/models/gp.hpp>

namespace loom
{

//----------------------------------------------------------------------------
// Generics

template<class Model>
struct BaseModel
{
    template<bool cached>
    struct Mixture
    {
        typedef typename std::conditional<
            cached,
            typename Model::CachedMixture,
            typename Model::SimpleMixture>::type t;
    };

    static Model * null ()
    {
        return static_cast<Model *>(nullptr);
    }
};

//----------------------------------------------------------------------------
// Models

struct Clustering : BaseModel<Clustering>
{
    typedef typename distributions::Clustering<int>::PitmanYor Model;
    typedef Model Shared;
    typedef Model::Mixture CachedMixture;
    typedef distributions::MixtureDriver<Model, int> SimpleMixture;
};

template<int max_dim>
struct DirichletDiscrete : BaseModel<DirichletDiscrete<max_dim>>
{
    typedef distributions::dirichlet_discrete::Value Value;
    typedef distributions::dirichlet_discrete::Shared<max_dim> Shared;
    typedef distributions::dirichlet_discrete::Group<max_dim> Group;
    typedef distributions::dirichlet_discrete::Sampler<max_dim> Sampler;
    typedef distributions::dirichlet_discrete::Mixture<max_dim> CachedMixture;
    typedef distributions::MixtureSlave<Shared> SimpleMixture;
};

struct DirichletProcessDiscrete : BaseModel<DirichletProcessDiscrete>
{
    typedef distributions::dirichlet_process_discrete::Value Value;
    typedef distributions::dirichlet_process_discrete::Shared Shared;
    typedef distributions::dirichlet_process_discrete::Group Group;
    typedef distributions::dirichlet_process_discrete::Sampler Sampler;
    typedef distributions::dirichlet_process_discrete::Mixture CachedMixture;
    typedef distributions::MixtureSlave<Shared> SimpleMixture;
};

struct GammaPoisson : BaseModel<GammaPoisson>
{
    typedef distributions::gamma_poisson::Value Value;
    typedef distributions::gamma_poisson::Shared Shared;
    typedef distributions::gamma_poisson::Group Group;
    typedef distributions::gamma_poisson::Sampler Sampler;
    typedef distributions::gamma_poisson::Mixture CachedMixture;
    typedef distributions::MixtureSlave<Shared> SimpleMixture;
};

struct NormalInverseChiSq : BaseModel<NormalInverseChiSq>
{
    typedef distributions::normal_inverse_chi_sq::Value Value;
    typedef distributions::normal_inverse_chi_sq::Shared Shared;
    typedef distributions::normal_inverse_chi_sq::Group Group;
    typedef distributions::normal_inverse_chi_sq::Sampler Sampler;
    typedef distributions::normal_inverse_chi_sq::Mixture CachedMixture;
    typedef distributions::MixtureSlave<Shared> SimpleMixture;
};

//----------------------------------------------------------------------------
// Feature types

typedef DirichletDiscrete<16> DD16;
typedef DirichletDiscrete<256> DD256;
typedef DirichletProcessDiscrete DPD;
typedef GammaPoisson GP;
typedef NormalInverseChiSq NICH;

template<class Fun>
inline void for_each_feature_type (Fun & fun)
{
    fun(DD16::null());
    fun(DD256::null());
    fun(DPD::null());
    fun(GP::null());
    fun(NICH::null());
}

template<class Fun>
inline bool for_some_feature_type (Fun & fun)
{
    return fun(DD16::null())
        or fun(DD256::null())
        or fun(DPD::null())
        or fun(GP::null())
        or fun(NICH::null());
}

template<class Derived>
class ForEachFeatureType
{
    typedef typename Derived::template Container<DD16>::t DD16s;
    typedef typename Derived::template Container<DD256>::t DD256s;
    typedef typename Derived::template Container<DPD>::t DPDs;
    typedef typename Derived::template Container<GP>::t GPs;
    typedef typename Derived::template Container<NICH>::t NICHs;

public:

    DD16s dd16;
    DD256s dd256;
    DPDs dpd;
    GPs gp;
    NICHs nich;

    DD16s & operator[] (DD16 *) { return dd16; }
    DD256s & operator[] (DD256 *) { return dd256; }
    DPDs & operator[] (DPD *) { return dpd; }
    GPs & operator[] (GP *) { return gp; }
    NICHs & operator[] (NICH *) { return nich; }

    const DD16s & operator[] (DD16 *) const { return dd16; }
    const DD256s & operator[] (DD256 *) const { return dd256; }
    const DPDs & operator[] (DPD *) const { return dpd; }
    const GPs & operator[] (GP *) const { return gp; }
    const NICHs & operator[] (NICH *) const { return nich; }
};

} // namespace loom
